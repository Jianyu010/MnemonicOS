from __future__ import annotations

from dataclasses import replace
from datetime import date
import sqlite3

from .config import AppConfig
from .embedding_providers import build_embedding_provider, embedding_model_key
from .graph import graph_expand
from .models import ArchiveCandidate, ArchivePolicyDecision, CandidatePool, RecallCandidate, RecallMetrics
from .query_parser import ParsedQuery, tokenize as _tokenize_query
from .semantics import cosine_similarity, vector_from_json


RAW_INTENT_TERMS = frozenset(
    {
        "raw",
        "original",
        "transcript",
        "quote",
        "quoted",
        "verbatim",
        "provenance",
        "source",
        "sources",
        "wording",
        "session",
    }
)

RAW_INTENT_PHRASES = (
    "show the original",
    "exact wording",
    "raw session",
    "give me the transcript",
    "show provenance",
)


def _query_terms_from_tokens(tokens: list[str]) -> str | None:
    filtered = [term for term in tokens if term != "*"]
    if not filtered:
        return None
    return " OR ".join(f'"{term.replace(chr(34), " ")}"' for term in filtered)


def _query_terms(parsed_query: ParsedQuery) -> str | None:
    combined: list[str] = []
    seen: set[str] = set()
    for token in [*parsed_query.tokens, *parsed_query.expanded_terms]:
        if token in seen:
            continue
        combined.append(token)
        seen.add(token)
    return _query_terms_from_tokens(combined)


def _seed_score(candidate: RecallCandidate) -> float:
    if candidate.exact_rank is not None:
        return 1_000_000.0 - candidate.exact_rank
    components = [
        candidate.learned_score or 0.0,
        candidate.semantic_score or 0.0,
        candidate.graph_score or 0.0,
    ]
    if candidate.bm25_rank is not None and candidate.bm25_rank > 0:
        components.append(1.0 / candidate.bm25_rank)
    return max(components) if components else 0.0


def _canonical_note_candidate(row: sqlite3.Row, *, channels: set[str]) -> RecallCandidate:
    return RecallCandidate(
        id=str(row["id"]),
        title=str(row["title"]),
        type=str(row["type"]),
        status=str(row["status"]) if row["status"] is not None else None,
        chain_id=str(row["chain_id"]) if row["chain_id"] is not None else None,
        lifecycle_state=str(row["lifecycle_state"]) if row["lifecycle_state"] is not None else None,
        body_path=str(row["body_path"]),
        summary=str(row["summary"] or ""),
        body=str(row["body"] or ""),
        channels=set(channels),
        updated_at=str(row["updated_at"] or ""),
        created_at=str(row["created_at"] or ""),
        last_verified_at=str(row["last_verified_at"] or ""),
    )


def run_exact_channel(connection: sqlite3.Connection, parsed_query: ParsedQuery, *, limit: int = 5) -> list[RecallCandidate]:
    exact_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in [parsed_query.raw, *parsed_query.anchor_terms]:
        normalized = term.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen_terms:
            continue
        exact_terms.append(normalized)
        seen_terms.add(key)

    candidates: list[RecallCandidate] = []
    seen_ids: set[str] = set()
    for term in exact_terms:
        rows = connection.execute(
            """
            SELECT DISTINCT
                n.id, n.title, n.type, n.status, n.chain_id, n.lifecycle_state,
                n.body_path, n.summary, ns.body, n.updated_at, n.created_at, n.last_verified_at
            FROM notes n
            JOIN notes_search ns ON ns.note_id = n.id
            LEFT JOIN aliases a ON a.note_id = n.id
            WHERE (
                   a.alias = ?
                OR n.id = ?
                OR lower(n.title) = lower(?)
            )
              AND (n.status IS NULL OR n.status != 'retired')
              AND (n.valid_to IS NULL OR n.valid_to > date('now'))
            ORDER BY n.updated_at DESC, n.title ASC
            """,
            (term, term, term),
        ).fetchall()
        for row in rows:
            note_id = str(row["id"])
            if note_id in seen_ids:
                continue
            candidate = _canonical_note_candidate(row, channels={"exact"})
            candidate.exact_rank = len(candidates) + 1
            candidates.append(candidate)
            seen_ids.add(note_id)
            if len(candidates) >= limit:
                return candidates
    return candidates


def run_bm25_channel(connection: sqlite3.Connection, parsed_query: ParsedQuery) -> list[RecallCandidate]:
    match_query = _query_terms(parsed_query)
    if not match_query:
        return []
    limit = parsed_query.top_k * 4
    rows = connection.execute(
        """
        SELECT
            n.id,
            n.title,
            n.type,
            n.status,
            n.chain_id,
            n.lifecycle_state,
            n.body_path,
            n.summary,
            ns.body,
            n.updated_at,
            n.created_at,
            n.last_verified_at,
            bm25(notes_fts) AS bm25_score
        FROM notes_fts
        JOIN notes_search ns ON ns.rowid = notes_fts.rowid
        JOIN notes n ON n.id = ns.note_id
        WHERE notes_fts MATCH ?
          AND (n.status IS NULL OR n.status != 'retired')
          AND (n.valid_to IS NULL OR n.valid_to > date('now'))
        ORDER BY bm25_score
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()
    candidates: list[RecallCandidate] = []
    for rank, row in enumerate(rows, start=1):
        candidate = _canonical_note_candidate(row, channels={"bm25"})
        candidate.bm25_rank = rank
        candidate.bm25_score = float(row["bm25_score"] or 0.0)
        candidates.append(candidate)
    return candidates


def run_semantic_channel(connection: sqlite3.Connection, config: AppConfig, parsed_query: ParsedQuery) -> list[RecallCandidate]:
    if not config.embeddings.enabled:
        return []
    limit = parsed_query.top_k * 4
    query_vector = build_embedding_provider(config).embed_texts([parsed_query.raw])[0]
    rows = connection.execute(
        """
        SELECT
            n.id,
            n.title,
            n.type,
            n.status,
            n.chain_id,
            n.lifecycle_state,
            n.body_path,
            n.summary,
            ns.body,
            n.updated_at,
            n.created_at,
            n.last_verified_at,
            nv.vector_json
        FROM note_vectors nv
        JOIN notes n ON n.id = nv.note_id
        JOIN notes_search ns ON ns.note_id = n.id
        WHERE nv.model = ?
          AND (n.status IS NULL OR n.status != 'retired')
          AND (n.valid_to IS NULL OR n.valid_to > date('now'))
        """,
        (embedding_model_key(config),),
    ).fetchall()
    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        score = cosine_similarity(query_vector, vector_from_json(str(row["vector_json"])))
        if score <= 0.0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates: list[RecallCandidate] = []
    for score, row in scored[:limit]:
        candidate = _canonical_note_candidate(row, channels={"semantic"})
        candidate.semantic_score = score
        candidates.append(candidate)
    return candidates


def run_graph_channel(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    direct_candidates: list[RecallCandidate],
) -> tuple[list[RecallCandidate], list[str]]:
    if not config.graph.enabled or not direct_candidates:
        return [], []
    focal_ids = [
        candidate.id
        for candidate in sorted(direct_candidates, key=_seed_score, reverse=True)[: config.graph.focal_limit]
    ]
    if not focal_ids:
        return [], []
    graph_rows = graph_expand(connection, focal_ids=focal_ids, limit=config.graph.expand_limit)
    candidates: list[RecallCandidate] = []
    for graph_hit in graph_rows:
        candidate = RecallCandidate(
            id=graph_hit.id,
            title=graph_hit.title,
            type=graph_hit.type,
            status=graph_hit.status,
            chain_id=None,
            lifecycle_state=None,
            body_path=graph_hit.body_path,
            summary=graph_hit.summary,
            body=graph_hit.body,
            channels={"graph"},
            relation_types=list(graph_hit.relation_types),
            graph_depth=1,
            seed_from_ids=list(focal_ids),
            graph_score=graph_hit.graph_score,
            updated_at=graph_hit.updated_at,
            created_at=graph_hit.created_at,
            last_verified_at=graph_hit.last_verified_at,
        )
        candidates.append(candidate)
    candidates.extend(_direct_candidates_with_graph_provenance(connection, direct_candidates=direct_candidates, focal_ids=focal_ids))
    return candidates, focal_ids


def _direct_candidates_with_graph_provenance(
    connection: sqlite3.Connection,
    *,
    direct_candidates: list[RecallCandidate],
    focal_ids: list[str],
) -> list[RecallCandidate]:
    if len(focal_ids) < 2:
        return []
    direct_by_id = {candidate.id: candidate for candidate in direct_candidates}
    focal_rank = {note_id: rank for rank, note_id in enumerate(focal_ids)}
    placeholders = ",".join("?" for _ in focal_ids)
    rows = connection.execute(
        f"""
        SELECT source_note_id, target_id, relation_type, weight
        FROM graph_edges
        WHERE source_note_id IN ({placeholders})
          AND target_id IN ({placeholders})
        """,
        focal_ids * 2,
    ).fetchall()
    by_candidate: dict[str, RecallCandidate] = {}
    for row in rows:
        source_id = str(row["source_note_id"])
        target_id = str(row["target_id"])
        relation_type = str(row["relation_type"])
        weight = float(row["weight"] or 0.0)
        pairs = (
            (source_id, target_id, relation_type),
            (target_id, source_id, f"reverse_{relation_type}"),
        )
        for candidate_id, seed_id, rel_type in pairs:
            candidate = direct_by_id.get(candidate_id)
            if candidate is None:
                continue
            if focal_rank.get(seed_id, 999) >= focal_rank.get(candidate_id, 999):
                continue
            existing = by_candidate.get(candidate_id)
            if existing is None:
                existing = replace(
                    candidate,
                    channels={"graph"},
                    relation_types=[],
                    seed_from_ids=[],
                    graph_depth=1,
                    graph_score=0.0,
                )
                by_candidate[candidate_id] = existing
            existing.graph_score = (existing.graph_score or 0.0) + weight
            if rel_type not in existing.relation_types:
                existing.relation_types.append(rel_type)
            if seed_id not in existing.seed_from_ids:
                existing.seed_from_ids.append(seed_id)
    return list(by_candidate.values())


def decide_archive_policy(
    parsed_query: ParsedQuery,
    *,
    include_archive_fallback: bool,
) -> ArchivePolicyDecision:
    raw_lower = parsed_query.raw.casefold()
    token_set = set(parsed_query.tokens)
    raw_intent = bool(token_set & RAW_INTENT_TERMS) or any(phrase in raw_lower for phrase in RAW_INTENT_PHRASES)
    if raw_intent:
        return ArchivePolicyDecision(
            allowed=True,
            mode="explicit_raw_intent",
            reasons=["query expresses explicit raw/provenance intent"],
        )
    if include_archive_fallback:
        return ArchivePolicyDecision(
            allowed=True,
            mode="fallback_only",
            reasons=["runtime archive fallback is enabled"],
        )
    return ArchivePolicyDecision(allowed=False, mode="disabled", reasons=["archive fallback disabled"])


def run_archive_channel(
    connection: sqlite3.Connection,
    parsed_query: ParsedQuery,
    *,
    archive_policy: ArchivePolicyDecision,
) -> list[ArchiveCandidate]:
    if not archive_policy.allowed:
        return []
    match_query = _query_terms(parsed_query)
    if not match_query:
        return []
    rows = connection.execute(
        """
        SELECT
            ac.id AS chunk_id,
            ac.chunk_index,
            ac.summary,
            ac.body,
            a.id AS archive_id,
            a.path AS archive_path,
            bm25(chunks_fts) AS bm25_score
        FROM chunks_fts
        JOIN archive_chunks ac ON ac.id = chunks_fts.rowid
        JOIN archive a ON a.id = ac.archive_id
        WHERE chunks_fts MATCH ?
        ORDER BY bm25_score
        LIMIT ?
        """,
        (match_query, parsed_query.top_k),
    ).fetchall()
    candidates: list[ArchiveCandidate] = []
    for rank, row in enumerate(rows, start=1):
        body = str(row["body"] or "")
        summary = str(row["summary"] or "")
        candidates.append(
            ArchiveCandidate(
                id=f"{row['archive_id']}#chunk-{row['chunk_index']}",
                archive_id=str(row["archive_id"]),
                chunk_index=int(row["chunk_index"]),
                archive_path=str(row["archive_path"]),
                summary=summary,
                excerpt=summary or body[:240],
                channels={"archive"},
                bm25_rank=rank,
                bm25_score=float(row["bm25_score"] or 0.0),
            )
        )
    return candidates


def merge_wiki_candidates(
    *,
    exact_candidates: list[RecallCandidate],
    bm25_candidates: list[RecallCandidate],
    semantic_candidates: list[RecallCandidate],
    graph_candidates: list[RecallCandidate],
) -> list[RecallCandidate]:
    merged: dict[str, RecallCandidate] = {}
    for candidate in [*exact_candidates, *bm25_candidates, *semantic_candidates, *graph_candidates]:
        existing = merged.get(candidate.id)
        if existing is None:
            merged[candidate.id] = replace(
                candidate,
                channels=set(candidate.channels),
                relation_types=list(candidate.relation_types),
                seed_from_ids=list(candidate.seed_from_ids),
            )
            continue
        existing.channels.update(candidate.channels)
        if existing.chain_id is None:
            existing.chain_id = candidate.chain_id
        if existing.lifecycle_state is None:
            existing.lifecycle_state = candidate.lifecycle_state
        if not existing.summary:
            existing.summary = candidate.summary
        if not existing.body:
            existing.body = candidate.body
        if existing.exact_rank is None:
            existing.exact_rank = candidate.exact_rank
        if existing.bm25_rank is None:
            existing.bm25_rank = candidate.bm25_rank
        if existing.bm25_score is None:
            existing.bm25_score = candidate.bm25_score
        if existing.semantic_score is None:
            existing.semantic_score = candidate.semantic_score
        if existing.graph_score is None:
            existing.graph_score = candidate.graph_score
        if existing.learned_score is None:
            existing.learned_score = candidate.learned_score
        if existing.graph_depth is None:
            existing.graph_depth = candidate.graph_depth
        for relation_type in candidate.relation_types:
            if relation_type not in existing.relation_types:
                existing.relation_types.append(relation_type)
        for seed_id in candidate.seed_from_ids:
            if seed_id not in existing.seed_from_ids:
                existing.seed_from_ids.append(seed_id)
        if candidate.updated_at and not existing.updated_at:
            existing.updated_at = candidate.updated_at
        if candidate.created_at and not existing.created_at:
            existing.created_at = candidate.created_at
        if candidate.last_verified_at and not existing.last_verified_at:
            existing.last_verified_at = candidate.last_verified_at
    return list(merged.values())


def recall(
    config: AppConfig,
    parsed_query: ParsedQuery,
    *,
    connection: sqlite3.Connection,
    include_archive_fallback: bool | None = None,
) -> CandidatePool:
    include_archive = config.retrieval.include_archive_fallback if include_archive_fallback is None else include_archive_fallback
    exact_candidates = run_exact_channel(connection, parsed_query)
    bm25_candidates = run_bm25_channel(connection, parsed_query)
    semantic_candidates = run_semantic_channel(connection, config, parsed_query)
    direct_candidates = merge_wiki_candidates(
        exact_candidates=exact_candidates,
        bm25_candidates=bm25_candidates,
        semantic_candidates=semantic_candidates,
        graph_candidates=[],
    )
    graph_candidates, graph_seed_ids = run_graph_channel(connection, config, direct_candidates=direct_candidates)
    wiki_candidates = merge_wiki_candidates(
        exact_candidates=exact_candidates,
        bm25_candidates=bm25_candidates,
        semantic_candidates=semantic_candidates,
        graph_candidates=graph_candidates,
    )
    archive_policy = decide_archive_policy(parsed_query, include_archive_fallback=include_archive)
    archive_candidates = run_archive_channel(connection, parsed_query, archive_policy=archive_policy)
    metrics = RecallMetrics(
        exact_count=len(exact_candidates),
        bm25_count=len(bm25_candidates),
        semantic_count=len(semantic_candidates),
        graph_count=len(graph_candidates),
        archive_count=len(archive_candidates),
        merged_wiki_count=len(wiki_candidates),
        seed_count=len(graph_seed_ids),
        graph_seed_ids=list(graph_seed_ids),
    )
    return CandidatePool(
        parsed_query=parsed_query,
        wiki_candidates=wiki_candidates,
        archive_candidates=archive_candidates,
        recall_metrics=metrics,
        archive_policy=archive_policy,
    )

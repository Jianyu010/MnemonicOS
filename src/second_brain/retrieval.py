from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
from datetime import date
import json
import re
import sqlite3

from .config import AppConfig
from .db import connect_db, run_migrations
from .graph import graph_expand
from .models import RetrieveHit, RetrieveResult
from .paths import VaultPaths
from .semantics import cosine_similarity, encode_text, vector_from_json


TOKEN_PATTERN = re.compile(r"[\w/-]+", re.UNICODE)
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "we",
    "what",
    "which",
    "who",
}

QUERY_HINTS = {
    "preference_identity": {"preference", "prefer", "style", "identity", "user"},
    "how_to": {"how", "steps", "deploy", "run", "procedure", "release"},
    "decision": {"why", "decision", "decide", "choose", "picked", "rationale"},
    "who_where_relation": {"who", "owner", "owns", "responsible", "maintainer"},
    "what_changed": {"changed", "recent", "latest", "new", "update"},
    "broad_synthesis": {"state", "summary", "overview", "status"},
}

TYPE_PRIORS = {
    "preference_identity": {"person": 0.9, "concept": 0.5},
    "how_to": {"procedure": 0.9, "repo": 0.6, "project": 0.4},
    "decision": {"decision": 0.9, "incident": 0.6, "project": 0.4},
    "who_where_relation": {"person": 0.9, "project": 0.7, "repo": 0.5},
    "what_changed": {"incident": 0.8, "project": 0.7, "decision": 0.6},
    "broad_synthesis": {"overview": 0.9, "project": 0.7, "concept": 0.5},
}


@dataclass(slots=True)
class _Candidate:
    id: str
    title: str
    type: str
    status: str | None
    body_path: str
    summary: str
    body: str
    channels: set[str]
    exact_rank: int | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    semantic_score: float | None = None
    graph_score: float | None = None
    graph_relations: list[str] | None = None
    updated_at: str | None = None
    created_at: str | None = None
    last_verified_at: str | None = None


def _query_terms(query: str) -> str:
    terms = [
        token
        for token in TOKEN_PATTERN.findall(query)
        if len(token) > 2 and token.casefold() not in STOPWORDS
    ]
    if not terms:
        escaped = query.replace('"', ' ')
        return f'"{escaped}"'
    unique_terms: list[str] = []
    seen: set[str] = set()
    for term in terms:
        lowered = term.casefold()
        if lowered in seen:
            continue
        unique_terms.append(term)
        seen.add(lowered)
    return " OR ".join(f'"{term.replace(chr(34), " ")}"' for term in unique_terms)


def _load_pinned_paths(config: AppConfig) -> list[str]:
    vault_paths = VaultPaths(config.paths.workspace_root, config.paths.vault_root)
    return [str(path.resolve()) for path in vault_paths.pinned_files() if path.exists()]


def _classify_query(query: str, hint: str | None) -> tuple[str | None, float | None]:
    if hint:
        return hint, 1.0
    tokens = {token.casefold() for token in TOKEN_PATTERN.findall(query)}
    best_type: str | None = None
    best_score = 0.0
    for query_type, keywords in QUERY_HINTS.items():
        score = len(tokens & keywords)
        if score > best_score:
            best_type = query_type
            best_score = float(score)
    if best_type is None or best_score == 0.0:
        return None, 0.0
    confidence = min(1.0, 0.45 + 0.2 * best_score)
    return best_type, confidence


def _exact_hits(connection: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT DISTINCT n.id, n.title, n.type, n.status, n.body_path, n.summary, ns.body,
               n.updated_at, n.created_at, n.last_verified_at
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
        (query, query, query),
    ).fetchall()
    return rows


def _bm25_hits(connection: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    match_query = _query_terms(query)
    rows = connection.execute(
        """
        SELECT
            n.id,
            n.title,
            n.type,
            n.status,
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
    return rows


def _semantic_hits(connection: sqlite3.Connection, config: AppConfig, query: str, limit: int) -> list[sqlite3.Row]:
    if not config.embeddings.enabled:
        return []
    query_vector = encode_text(query, dimensions=config.embeddings.dimensions)
    rows = connection.execute(
        """
        SELECT
            n.id,
            n.title,
            n.type,
            n.status,
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
        (config.embeddings.model,),
    ).fetchall()

    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        score = cosine_similarity(query_vector, vector_from_json(str(row["vector_json"])))
        if score <= 0.0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:limit]]


def _archive_hits(connection: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    match_query = _query_terms(query)
    return connection.execute(
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
        (match_query, limit),
    ).fetchall()


def _freshness_score(candidate: _Candidate) -> float:
    raw_date = candidate.last_verified_at or candidate.updated_at or candidate.created_at
    if not raw_date:
        return 0.2
    try:
        year, month, day = (int(part) for part in raw_date.split("-")[:3])
        delta = (date.today() - date(year, month, day)).days
    except Exception:
        return 0.2
    return max(0.0, 1.0 - min(delta, 365) / 365.0)


def _memory_strength(connection: sqlite3.Connection, note_ids: list[str]) -> dict[str, float]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" for _ in note_ids)
    rows = connection.execute(
        f"""
        SELECT note_id, COUNT(*) AS hit_count
        FROM retrieval_hits
        WHERE note_id IN ({placeholders}) AND selected = 1
        GROUP BY note_id
        """,
        note_ids,
    ).fetchall()
    counts = {str(row["note_id"]): int(row["hit_count"]) for row in rows}
    return {note_id: min(1.0, counts.get(note_id, 0) / 5.0) for note_id in note_ids}


def _type_prior(query_type: str | None, note_type: str) -> float:
    if query_type is None:
        return 0.0
    return TYPE_PRIORS.get(query_type, {}).get(note_type, 0.0)


def _seed_score(candidate: _Candidate) -> float:
    if candidate.exact_rank is not None:
        return 1_000_000.0 - candidate.exact_rank
    bm25_component = 0.0
    if candidate.bm25_rank is not None:
        bm25_component = 1.0 / candidate.bm25_rank
    semantic_component = max(candidate.semantic_score or 0.0, 0.0)
    return bm25_component + semantic_component


def retrieve(
    config: AppConfig,
    query: str,
    *,
    top_k: int | None = None,
    query_type_hint: str | None = None,
) -> RetrieveResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")

    requested_top_k = top_k or config.retrieval.top_k
    query_type, classifier_confidence = _classify_query(query, query_type_hint)
    candidates: dict[str, _Candidate] = {}

    exact_rows = _exact_hits(connection, query)
    for rank, row in enumerate(exact_rows, start=1):
        candidates[str(row["id"])] = _Candidate(
            id=str(row["id"]),
            title=str(row["title"]),
            type=str(row["type"]),
            status=str(row["status"]) if row["status"] is not None else None,
            body_path=str(row["body_path"]),
            summary=str(row["summary"] or ""),
            body=str(row["body"] or ""),
            channels={"exact"},
            exact_rank=rank,
            updated_at=str(row["updated_at"] or ""),
            created_at=str(row["created_at"] or ""),
            last_verified_at=str(row["last_verified_at"] or ""),
        )

    bm25_rows = _bm25_hits(connection, query, requested_top_k * 4)
    for rank, row in enumerate(bm25_rows, start=1):
        note_id = str(row["id"])
        bm25_score = float(row["bm25_score"] or 0.0)
        if note_id in candidates:
            candidate = candidates[note_id]
            candidate.channels.add("bm25")
            candidate.bm25_rank = rank
            candidate.bm25_score = bm25_score
            continue
        candidates[note_id] = _Candidate(
            id=note_id,
            title=str(row["title"]),
            type=str(row["type"]),
            status=str(row["status"]) if row["status"] is not None else None,
            body_path=str(row["body_path"]),
            summary=str(row["summary"] or ""),
            body=str(row["body"] or ""),
            channels={"bm25"},
            bm25_rank=rank,
            bm25_score=bm25_score,
            updated_at=str(row["updated_at"] or ""),
            created_at=str(row["created_at"] or ""),
            last_verified_at=str(row["last_verified_at"] or ""),
        )

    semantic_rows = _semantic_hits(connection, config, query, requested_top_k * 4)
    query_vector = encode_text(query, dimensions=config.embeddings.dimensions) if config.embeddings.enabled else []
    for row in semantic_rows:
        note_id = str(row["id"])
        semantic_score = cosine_similarity(query_vector, vector_from_json(str(row["vector_json"])))
        if note_id in candidates:
            candidate = candidates[note_id]
            candidate.channels.add("semantic")
            candidate.semantic_score = semantic_score
            continue
        candidates[note_id] = _Candidate(
            id=note_id,
            title=str(row["title"]),
            type=str(row["type"]),
            status=str(row["status"]) if row["status"] is not None else None,
            body_path=str(row["body_path"]),
            summary=str(row["summary"] or ""),
            body=str(row["body"] or ""),
            channels={"semantic"},
            semantic_score=semantic_score,
            updated_at=str(row["updated_at"] or ""),
            created_at=str(row["created_at"] or ""),
            last_verified_at=str(row["last_verified_at"] or ""),
        )

    if config.graph.enabled and candidates:
        focal_ids = [
            candidate.id
            for candidate in sorted(candidates.values(), key=_seed_score, reverse=True)[: config.graph.focal_limit]
        ]
        graph_rows = graph_expand(connection, focal_ids=focal_ids, limit=config.graph.expand_limit)
        for graph_hit in graph_rows:
            if graph_hit.id in candidates:
                candidate = candidates[graph_hit.id]
                candidate.channels.add("graph")
                candidate.graph_score = graph_hit.graph_score
                candidate.graph_relations = graph_hit.relation_types
                continue
            candidates[graph_hit.id] = _Candidate(
                id=graph_hit.id,
                title=graph_hit.title,
                type=graph_hit.type,
                status=graph_hit.status,
                body_path=graph_hit.body_path,
                summary=graph_hit.summary,
                body=graph_hit.body,
                channels={"graph"},
                graph_score=graph_hit.graph_score,
                graph_relations=graph_hit.relation_types,
                updated_at=graph_hit.updated_at,
                created_at=graph_hit.created_at,
                last_verified_at=graph_hit.last_verified_at,
            )

    memory_strengths = _memory_strength(connection, list(candidates))
    ranked_hits: list[RetrieveHit] = []
    bm25_max_rank = max(((candidate.bm25_rank or 0) for candidate in candidates.values()), default=0) or 1
    graph_max_score = max(((candidate.graph_score or 0.0) for candidate in candidates.values()), default=0.0) or 1.0
    for candidate in candidates.values():
        if candidate.exact_rank is not None:
            final_score = 1_000_000.0 - candidate.exact_rank
            raw_score = 1.0
        else:
            bm25_norm = 0.0
            if candidate.bm25_rank is not None:
                bm25_norm = 1.0 - ((candidate.bm25_rank - 1) / bm25_max_rank)
            semantic_norm = max(candidate.semantic_score or 0.0, 0.0)
            graph_norm = max(candidate.graph_score or 0.0, 0.0) / graph_max_score
            if query_type == "what_changed":
                freshness_weight = 0.25
                remainder = 1.0 - freshness_weight - config.retrieval.exact_alias_weight - config.graph.graph_weight
                bm25_weight = max(0.0, remainder * 0.45)
                semantic_weight = max(0.0, remainder * 0.35)
                type_prior_weight = max(0.0, remainder * 0.10)
                memory_weight = max(0.0, remainder * 0.10)
            else:
                freshness_weight = config.retrieval.freshness_weight
                bm25_weight = config.retrieval.bm25_weight
                semantic_weight = config.retrieval.semantic_weight
                type_prior_weight = config.retrieval.type_prior_weight
                memory_weight = config.retrieval.memory_strength_weight

            exact_bonus = 1.0 if "exact" in candidate.channels else 0.0
            final_score = (
                bm25_weight * bm25_norm
                + semantic_weight * semantic_norm
                + config.graph.graph_weight * graph_norm
                + type_prior_weight * _type_prior(query_type, candidate.type)
                + freshness_weight * _freshness_score(candidate)
                + memory_weight * memory_strengths.get(candidate.id, 0.0)
                + config.retrieval.exact_alias_weight * exact_bonus
            )
            if candidate.graph_score is not None and candidate.semantic_score is None and candidate.bm25_score is None:
                raw_score = candidate.graph_score
            else:
                raw_score = candidate.semantic_score if candidate.semantic_score is not None else candidate.bm25_score

        excerpt = candidate.summary or candidate.body[:240]
        if candidate.graph_relations:
            relation_summary = ", ".join(candidate.graph_relations[:3])
            if relation_summary:
                excerpt = f"{excerpt} | graph: {relation_summary}".strip(" |")
        ranked_hits.append(
            RetrieveHit(
                id=candidate.id,
                title=candidate.title,
                type=candidate.type,
                status=candidate.status,
                body_path=candidate.body_path,
                summary=candidate.summary,
                channels=sorted(candidate.channels),
                raw_score=raw_score,
                final_score=final_score,
                excerpt=excerpt,
            )
        )

    ranked_hits = sorted(ranked_hits, key=lambda hit: (-hit.final_score, hit.title.casefold()))[:requested_top_k]

    archive_hits: list[RetrieveHit] = []
    if not ranked_hits and config.retrieval.include_archive_fallback:
        for rank, row in enumerate(_archive_hits(connection, query, requested_top_k), start=1):
            score = 1.0 - ((rank - 1) / max(requested_top_k, 1))
            archive_hits.append(
                RetrieveHit(
                    id=f"{row['archive_id']}#chunk-{row['chunk_index']}",
                    title=f"Archive chunk {row['chunk_index']}",
                    type="archive_chunk",
                    status=None,
                    body_path=str(row["archive_path"]),
                    summary=str(row["summary"] or ""),
                    channels=["archive"],
                    raw_score=float(row["bm25_score"] or 0.0),
                    final_score=score,
                    excerpt=str(row["summary"] or row["body"][:240]),
                )
            )
        ranked_hits = archive_hits

    with connection:
        cursor = connection.execute(
            """
            INSERT INTO retrieval_queries(query, query_type, classifier_confidence, retrieved, top_k)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                query,
                query_type,
                classifier_confidence,
                json.dumps([hit.id for hit in ranked_hits]),
                requested_top_k,
            ),
        )
        query_id = int(cursor.lastrowid)

        for rank, hit in enumerate(ranked_hits, start=1):
            if hit.type == "archive_chunk":
                chunk_match = re.search(r"#chunk-(\d+)$", hit.id)
                chunk_index = int(chunk_match.group(1)) if chunk_match else None
                if chunk_index is not None:
                    chunk_row = connection.execute(
                        """
                        SELECT ac.id
                        FROM archive_chunks ac
                        JOIN archive a ON a.id = ac.archive_id
                        WHERE a.id = ? AND ac.chunk_index = ?
                        """,
                        (hit.id.split("#", 1)[0], chunk_index),
                    ).fetchone()
                    if chunk_row is not None:
                        connection.execute(
                            """
                            INSERT INTO retrieval_archive_hits(query_id, chunk_id, rank, channel, raw_score, final_score)
                            VALUES (?, ?, ?, 'archive_bm25', ?, ?)
                            """,
                            (query_id, int(chunk_row["id"]), rank, hit.raw_score, hit.final_score),
                        )
                continue
            primary_channel = (
                "exact"
                if "exact" in hit.channels
                else "semantic"
                if "semantic" in hit.channels
                else "graph"
                if "graph" in hit.channels
                else "bm25"
            )
            connection.execute(
                """
                INSERT INTO retrieval_hits(query_id, note_id, rank, channel, raw_score, final_score, selected)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (query_id, hit.id, rank, primary_channel, hit.raw_score, hit.final_score),
            )

    connection.close()
    return RetrieveResult(
        query_id=query_id,
        query_type=query_type,
        classifier_confidence=classifier_confidence,
        hits=ranked_hits,
        pinned_paths=_load_pinned_paths(config),
    )


def result_to_json(result: RetrieveResult) -> str:
    payload = {
        "query_id": result.query_id,
        "query_type": result.query_type,
        "classifier_confidence": result.classifier_confidence,
        "pinned_paths": result.pinned_paths,
        "hits": [asdict(hit) for hit in result.hits],
    }
    return json.dumps(payload, indent=2)

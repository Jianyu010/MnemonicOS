from __future__ import annotations

from dataclasses import asdict
import json
import re

from .config import AppConfig
from .db import connect_db, run_migrations
from .models import RetrieveHit, RetrieveResult, RecallCandidate
from .paths import VaultPaths
from .query_parser import QueryHints, parse_query
from .recall import recall
from .signal_integration import integrate_signals
from .trust import load_rerank_model


def _load_pinned_paths(config: AppConfig) -> list[str]:
    vault_paths = VaultPaths(config.paths.workspace_root, config.paths.vault_root)
    return [str(path.resolve()) for path in vault_paths.pinned_files() if path.exists()]


def retrieve(
    config: AppConfig,
    query: str,
    *,
    top_k: int | None = None,
    query_type_hint: str | None = None,
    include_archive_fallback: bool | None = None,
    temporality_override: str | None = None,
    bridge_id: str | None = None,
    caller_type_hint: str | None = None,
) -> RetrieveResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")

    parsed_query = parse_query(
        query,
        QueryHints(
            query_type_hint=query_type_hint,
            temporality_override=temporality_override,
            top_k=top_k,
            caller_type_hint=caller_type_hint,
        ),
        config=config,
        connection=connection,
        bridge_id=bridge_id,
    )
    requested_top_k = parsed_query.top_k
    query_type = parsed_query.query_type
    classifier_confidence = parsed_query.classifier_confidence
    temporality = parsed_query.temporality
    candidate_pool = recall(
        config,
        parsed_query,
        connection=connection,
        include_archive_fallback=include_archive_fallback,
    )
    enriched_pool = integrate_signals(config, parsed_query, candidate_pool, connection=connection)
    candidates: dict[str, RecallCandidate] = {
        item.candidate.id: item.candidate for item in enriched_pool.wiki_candidates
    }
    signal_map = {item.candidate.id: item.signals for item in enriched_pool.wiki_candidates}

    rerank_model = load_rerank_model(connection)
    ranked_hits: list[RetrieveHit] = []
    bm25_max_rank = max(((candidate.bm25_rank or 0) for candidate in candidates.values()), default=0) or 1
    graph_max_score = max(((candidate.graph_score or 0.0) for candidate in candidates.values()), default=0.0) or 1.0
    for enriched_candidate in enriched_pool.wiki_candidates:
        candidate = enriched_candidate.candidate
        signals = enriched_candidate.signals
        trust_score = signals.trust_score
        freshness_score = signals.freshness_score
        freshness_state = signals.freshness_state
        type_prior_score = signals.type_prior_score

        if candidate.exact_rank is not None:
            final_score = 1_000_000.0 - candidate.exact_rank
            raw_score = 1.0
        else:
            bm25_norm = 0.0
            if candidate.bm25_rank is not None:
                bm25_norm = 1.0 - ((candidate.bm25_rank - 1) / bm25_max_rank)
            semantic_norm = max(
                candidate.learned_score if candidate.learned_score is not None else (candidate.semantic_score or 0.0),
                0.0,
            )
            graph_norm = max(candidate.graph_score or 0.0, 0.0) / graph_max_score
            freshness_weight = rerank_model.weights.get("freshness_weight", config.retrieval.freshness_weight)
            bm25_weight = rerank_model.weights.get("bm25_weight", config.retrieval.bm25_weight)
            semantic_weight = rerank_model.weights.get("semantic_weight", config.retrieval.semantic_weight)
            graph_weight = rerank_model.weights.get("graph_weight", config.graph.graph_weight)
            type_prior_weight = rerank_model.weights.get("type_prior_weight", config.retrieval.type_prior_weight)
            trust_weight = rerank_model.weights.get("trust_weight", config.retrieval.memory_strength_weight)
            if query_type is not None and (classifier_confidence or 0.0) >= 1.0:
                type_prior_weight = max(type_prior_weight, 0.25)
            if query_type == "what_changed":
                freshness_weight = max(freshness_weight, 0.25)

            exact_bonus = 1.0 if "exact" in candidate.channels else 0.0
            freshness_multiplier_value = signals.freshness_multiplier
            historical_bonus = 0.0
            if temporality == "historical":
                if freshness_state == "stale":
                    historical_bonus = 0.20
                elif freshness_state == "suspect":
                    historical_bonus = 0.12
                elif freshness_state == "aging":
                    historical_bonus = 0.06
            final_score = (
                bm25_weight * bm25_norm
                + semantic_weight * semantic_norm
                + graph_weight * graph_norm
                + type_prior_weight * type_prior_score
                + freshness_weight * freshness_score
                + trust_weight * trust_score
                + config.retrieval.exact_alias_weight * exact_bonus
                + historical_bonus
            ) * freshness_multiplier_value
            if candidate.graph_score is not None and candidate.semantic_score is None and candidate.learned_score is None and candidate.bm25_score is None:
                raw_score = candidate.graph_score
            else:
                raw_score = (
                    candidate.learned_score
                    if candidate.learned_score is not None
                    else candidate.semantic_score
                    if candidate.semantic_score is not None
                    else candidate.bm25_score
                )

        excerpt = candidate.summary or candidate.body[:240]
        if candidate.relation_types:
            relation_summary = ", ".join(candidate.relation_types[:3])
            if relation_summary:
                excerpt = f"{excerpt} | graph: {relation_summary}".strip(" |")
        if freshness_state not in {"fresh", ""}:
            excerpt = f"{excerpt} | freshness: {freshness_state}".strip(" |")
        ranked_hits.append(
            RetrieveHit(
                id=candidate.id,
                title=candidate.title,
                type=candidate.type,
                status=candidate.status,
                chain_id=candidate.chain_id,
                freshness_state=freshness_state,
                lifecycle_state=candidate.lifecycle_state,
                body_path=candidate.body_path,
                summary=candidate.summary,
                channels=sorted(candidate.channels),
                raw_score=raw_score,
                final_score=final_score,
                excerpt=excerpt,
                relation_types=list(candidate.relation_types or []),
            )
        )

    ranked_hits = sorted(ranked_hits, key=lambda hit: (-hit.final_score, hit.title.casefold()))[:requested_top_k]

    archive_hits: list[RetrieveHit] = []
    if not ranked_hits and enriched_pool.archive_candidates:
        for archive_item in enriched_pool.archive_candidates:
            archive_candidate = archive_item.candidate
            score = 1.0 - (((archive_candidate.bm25_rank or 1) - 1) / max(requested_top_k, 1))
            archive_hits.append(
                RetrieveHit(
                    id=archive_candidate.id,
                    title=f"Archive chunk {archive_candidate.chunk_index}",
                    type="archive_chunk",
                    status=None,
                    chain_id=None,
                    freshness_state=None,
                    lifecycle_state=None,
                    body_path=archive_candidate.archive_path,
                    summary=archive_candidate.summary,
                    channels=["archive"],
                    raw_score=archive_candidate.bm25_score,
                    final_score=score,
                    excerpt=archive_candidate.excerpt,
                    relation_types=[],
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
                else "learned"
                if "learned" in hit.channels
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
            original = candidates.get(hit.id)
            if original is not None:
                signals = signal_map[hit.id]
                connection.execute(
                    """
                    INSERT INTO retrieval_hit_features(
                        query_id, note_id, exact_hit, bm25_rank, bm25_score, semantic_score,
                        graph_score, freshness_score, type_prior_score, trust_score, importance_score
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(query_id, note_id) DO UPDATE SET
                        exact_hit = excluded.exact_hit,
                        bm25_rank = excluded.bm25_rank,
                        bm25_score = excluded.bm25_score,
                        semantic_score = excluded.semantic_score,
                        graph_score = excluded.graph_score,
                        freshness_score = excluded.freshness_score,
                        type_prior_score = excluded.type_prior_score,
                        trust_score = excluded.trust_score,
                        importance_score = excluded.importance_score
                    """,
                    (
                        query_id,
                        hit.id,
                        1 if original.exact_rank is not None else 0,
                        original.bm25_rank,
                        original.bm25_score,
                        original.learned_score if original.learned_score is not None else original.semantic_score,
                        original.graph_score,
                        signals.freshness_score,
                        signals.type_prior_score,
                        signals.trust_score,
                        signals.importance_score,
                    ),
                )

    connection.close()
    return RetrieveResult(
        query_id=query_id,
        query_type=query_type,
        classifier_confidence=classifier_confidence,
        hits=ranked_hits,
        pinned_paths=_load_pinned_paths(config),
        temporality=parsed_query.temporality,
        persona=parsed_query.persona,
        back_feed_hook=parsed_query.back_feed_hook,
    )


def result_to_json(result: RetrieveResult) -> str:
    payload = {
        "query_id": result.query_id,
        "query_type": result.query_type,
        "classifier_confidence": result.classifier_confidence,
        "temporality": result.temporality,
        "persona": result.persona,
        "back_feed_hook": result.back_feed_hook,
        "pinned_paths": result.pinned_paths,
        "hits": [asdict(hit) for hit in result.hits],
    }
    return json.dumps(payload, indent=2)

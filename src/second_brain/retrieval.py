from __future__ import annotations

from dataclasses import asdict
import json
import re

from .config import AppConfig
from .db import connect_db, run_migrations
from .models import RetrieveResult
from .paths import VaultPaths
from .query_parser import QueryHints, parse_query
from .recall import recall
from .reranker import rerank
from .signal_integration import integrate_signals


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
    ranked = rerank(config, enriched_pool, connection=connection, top_k=requested_top_k)
    ranked_hits = ranked.hits
    archive_hits = [item.hit for item in ranked.archive_hits]
    feature_rows = {item.hit.id: item.feature_row for item in ranked.wiki_hits}

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
            feature_row = feature_rows.get(hit.id)
            if feature_row is not None:
                connection.execute(
                    """
                    INSERT INTO retrieval_hit_features(
                        query_id, note_id, exact_hit, bm25_rank, bm25_score, semantic_score,
                        graph_score, freshness_score, type_prior_score, trust_score, importance_score,
                        quality_pass, quality_score, passive_feedback_state, passive_feedback_confidence
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(query_id, note_id) DO UPDATE SET
                        exact_hit = excluded.exact_hit,
                        bm25_rank = excluded.bm25_rank,
                        bm25_score = excluded.bm25_score,
                        semantic_score = excluded.semantic_score,
                        graph_score = excluded.graph_score,
                        freshness_score = excluded.freshness_score,
                        type_prior_score = excluded.type_prior_score,
                        trust_score = excluded.trust_score,
                        importance_score = excluded.importance_score,
                        quality_pass = excluded.quality_pass,
                        quality_score = excluded.quality_score,
                        passive_feedback_state = excluded.passive_feedback_state,
                        passive_feedback_confidence = excluded.passive_feedback_confidence
                    """,
                    (
                        query_id,
                        hit.id,
                        feature_row.exact_hit,
                        feature_row.bm25_rank,
                        feature_row.bm25_score,
                        feature_row.semantic_score,
                        feature_row.graph_score,
                        feature_row.freshness_score,
                        feature_row.type_prior_score,
                        feature_row.trust_score,
                        feature_row.importance_score,
                        1 if feature_row.quality_pass else 0,
                        feature_row.quality_score,
                        feature_row.passive_feedback_state,
                        feature_row.passive_feedback_confidence,
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
        archive_hits=archive_hits,
        suppressed_by_gate_count=ranked.suppressed_by_gate_count,
        passed_gate_count=ranked.passed_gate_count,
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
        "archive_hits": [asdict(hit) for hit in result.archive_hits],
        "suppressed_by_gate_count": result.suppressed_by_gate_count,
        "passed_gate_count": result.passed_gate_count,
    }
    return json.dumps(payload, indent=2)

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .config import AppConfig
from .db import connect_db, run_migrations
from .extraction import extraction_status, process_extraction_queue
from .models import JobRunResult
from .promotion import process_promotion_queue
from .sync import sync_vault
from .semantics import encode_text, upsert_archive_chunk_vector


def _open_job_run(connection, job_name: str, mode: str) -> int:
    cursor = connection.execute(
        "INSERT INTO job_runs(job_name, mode, status) VALUES (?, ?, 'started')",
        (job_name, mode),
    )
    return int(cursor.lastrowid)


def _close_job_run(connection, job_id: int, *, status: str, stats: dict[str, Any], error_text: str | None = None) -> None:
    connection.execute(
        """
        UPDATE job_runs
        SET status = ?, finished_at = datetime('now'), stats_json = ?, error_text = ?
        WHERE id = ?
        """,
        (status, json.dumps(stats), error_text, job_id),
    )


def _to_iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collect_queue_stats(config: AppConfig) -> dict[str, Any]:
    status = extraction_status(config)
    queued_at_raw = status.get("oldest_pending_queued_at")
    oldest_pending_age_minutes: int | None = None
    if queued_at_raw:
        queued_at = datetime.fromisoformat(str(queued_at_raw).replace("Z", "+00:00"))
        if queued_at.tzinfo is None:
            queued_at = queued_at.astimezone()
        oldest_pending_age_minutes = max(
            0,
            int((datetime.now().astimezone() - queued_at.astimezone()).total_seconds() // 60),
        )
    pending = int(status.get("pending_count") or 0)
    warnings: list[str] = []
    daemon = config.daemon
    if daemon is not None:
        if pending > daemon.max_pending:
            warnings.append(f"extraction_queue depth {pending} exceeds threshold {daemon.max_pending}")
        if oldest_pending_age_minutes is not None and oldest_pending_age_minutes > daemon.max_oldest_age_minutes:
            warnings.append(
                "oldest pending extraction item age "
                f"{oldest_pending_age_minutes}m exceeds threshold {daemon.max_oldest_age_minutes}m"
            )
    return {
        "queue_counts": dict(status.get("queue_counts") or {}),
        "pending_count": pending,
        "error_count": int(status.get("error_count") or 0),
        "oldest_pending_age_minutes": oldest_pending_age_minutes,
        "warnings": warnings,
    }


def collect_storage_stats(config: AppConfig) -> dict[str, Any]:
    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                total += child.stat().st_size
        return total

    db_bytes = config.paths.db_path.stat().st_size if config.paths.db_path.exists() else 0
    vault_bytes = _dir_size(config.paths.vault_root)
    vectors_bytes = _dir_size(config.paths.vectors_dir)
    graph_bytes = _dir_size(config.paths.graph_dir)
    return {
        "db_bytes": db_bytes,
        "vault_bytes": vault_bytes,
        "vectors_bytes": vectors_bytes,
        "graph_bytes": graph_bytes,
    }


def promotion_status(config: AppConfig) -> dict[str, Any]:
    """Return promotion queue health counts from promotion_decisions + extraction_candidates."""
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    try:
        promotion_ready = int(
            (connection.execute(
                "SELECT COUNT(*) FROM extraction_candidates WHERE promotion_state = 'promotion_ready'"
            ).fetchone() or [0])[0]
        )
        state_rows = connection.execute(
            "SELECT promotion_state, COUNT(*) AS cnt FROM promotion_decisions GROUP BY promotion_state"
        ).fetchall()
        state_counts = {str(r["promotion_state"]): int(r["cnt"]) for r in state_rows}
        last_run_row = connection.execute(
            """
            SELECT finished_at FROM job_runs
            WHERE job_name = 'process_promotion_queue' AND finished_at IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    return {
        "promotion_ready": promotion_ready,
        "promoted": state_counts.get("promoted", 0),
        "review_routed": state_counts.get("review_routed", 0),
        "rejected": state_counts.get("rejected", 0),
        "last_run_at": str(last_run_row["finished_at"]) if last_run_row else None,
    }


def collect_status_snapshot(config: AppConfig) -> dict[str, Any]:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    last_runs = connection.execute(
        """
        SELECT job_name, status, finished_at
        FROM job_runs
        WHERE finished_at IS NOT NULL
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    connection.close()
    snapshot = {
        "queue": collect_queue_stats(config),
        "promotion": promotion_status(config),
        "storage": collect_storage_stats(config),
        "recent_job_runs": [
            {
                "job_name": str(row["job_name"]),
                "status": str(row["status"]),
                "finished_at": str(row["finished_at"]),
            }
            for row in last_runs
        ],
    }
    return snapshot



def _embed_backfill(config: AppConfig, *, dry_run: bool = False) -> JobRunResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    stats: dict[str, int] = {"archive_chunks_embedded": 0}
    try:
        with connection:
            job_id = _open_job_run(connection, "embed_backfill", "dry-run" if dry_run else "live")
        if not config.embeddings.enabled:
            stats["embeddings_disabled"] = 1
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
            return JobRunResult(job_name="embed_backfill", status="ok", stats=stats, review_items_created=[])

        rows = connection.execute(
            """
            SELECT ac.id, ac.body, ac.summary, ac.content_hash
            FROM archive_chunks ac
            LEFT JOIN archive_chunk_vectors acv
              ON acv.chunk_id = ac.id AND acv.model = ?
            WHERE acv.chunk_id IS NULL
            ORDER BY ac.id ASC
            """,
            (config.embeddings.model,),
        ).fetchall()
        if dry_run:
            stats["archive_chunks_pending"] = len(rows)
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
            return JobRunResult(job_name="embed_backfill", status="ok", stats=stats, review_items_created=[])

        with connection:
            for row in rows:
                vector = encode_text(
                    str(row["summary"] or row["body"] or ""),
                    dimensions=config.embeddings.dimensions,
                )
                upsert_archive_chunk_vector(
                    connection,
                    chunk_id=int(row["id"]),
                    model=config.embeddings.model,
                    dimensions=config.embeddings.dimensions,
                    vector=vector,
                    source_hash=str(row["content_hash"]),
                )
                stats["archive_chunks_embedded"] += 1
            _close_job_run(connection, job_id, status="ok", stats=stats)
        return JobRunResult(job_name="embed_backfill", status="ok", stats=stats, review_items_created=[])
    except Exception as exc:
        with connection:
            _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
        raise
    finally:
        connection.close()


def _status_report_job(config: AppConfig, *, dry_run: bool = False) -> JobRunResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    snapshot = collect_status_snapshot(config)
    stats = {
        "pending_count": int(snapshot["queue"]["pending_count"]),
        "error_count": int(snapshot["queue"]["error_count"]),
        "warning_count": len(snapshot["queue"]["warnings"]),
        "db_bytes": int(snapshot["storage"]["db_bytes"]),
        "vault_bytes": int(snapshot["storage"]["vault_bytes"]),
        "vectors_bytes": int(snapshot["storage"]["vectors_bytes"]),
        "graph_bytes": int(snapshot["storage"]["graph_bytes"]),
    }
    with connection:
        job_id = _open_job_run(connection, "status_report", "dry-run" if dry_run else "live")
        _close_job_run(connection, job_id, status="ok", stats=stats)
    connection.close()
    return JobRunResult(job_name="status_report", status="ok", stats=stats, review_items_created=[])


def _process_extraction_queue_job(config: AppConfig, *, dry_run: bool = False) -> JobRunResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    stats: dict[str, int] = {}
    with connection:
        job_id = _open_job_run(connection, "process_extraction_queue", "dry-run" if dry_run else "live")
    connection.close()
    try:
        result = process_extraction_queue(config, dry_run=dry_run)
        stats = result.to_stats()
        connection = connect_db(config.paths.db_path)
        with connection:
            _close_job_run(connection, job_id, status="ok", stats=stats)
        connection.close()
        return JobRunResult(
            job_name="process_extraction_queue",
            status="ok",
            stats=stats,
            review_items_created=[],
        )
    except Exception as exc:
        connection = connect_db(config.paths.db_path)
        with connection:
            _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
        connection.close()
        raise


def _process_promotion_queue_job(config: AppConfig, *, dry_run: bool = False) -> JobRunResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    stats: dict[str, int] = {}
    with connection:
        job_id = _open_job_run(connection, "process_promotion_queue", "dry-run" if dry_run else "live")
    connection.close()
    try:
        result = process_promotion_queue(config, dry_run=dry_run)
        stats = result.to_stats()
        connection = connect_db(config.paths.db_path)
        with connection:
            _close_job_run(connection, job_id, status="ok", stats=stats)
        connection.close()

        return JobRunResult(
            job_name="process_promotion_queue",
            status="ok",
            stats=stats,
            review_items_created=[],
        )
    except Exception as exc:
        connection = connect_db(config.paths.db_path)
        with connection:
            _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
        connection.close()
        raise


def _expire_stale_episodes_job(config: AppConfig, *, dry_run: bool = False) -> JobRunResult:
    from .session_capture import end_episode
    from .models import EndEpisodeRequest
    threshold = config.daemon.stale_episode_threshold_hours if config.daemon is not None else 2
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    try:
        rows = connection.execute(
            """
            SELECT conversation_id, episode_id, bridge_id
            FROM raw_episodes
            WHERE status = 'active'
              AND updated_at < datetime('now', '-' || ? || ' hours')
            ORDER BY updated_at ASC
            """,
            (threshold,),
        ).fetchall()
    finally:
        connection.close()
    closed = 0
    errors = 0
    for row in rows:
        if dry_run:
            closed += 1
            continue
        try:
            end_episode(
                config,
                EndEpisodeRequest(
                    bridge_id=str(row["bridge_id"] or "job:expire"),
                    conversation_id=str(row["conversation_id"]),
                    episode_id=str(row["episode_id"]),
                    status="partial",
                    metadata={"expired_by": "expire_stale_episodes"},
                ),
            )
            closed += 1
        except Exception:
            errors += 1
    return JobRunResult(
        job_name="expire_stale_episodes",
        status="ok",
        stats={"closed": closed, "errors": errors},
        review_items_created=[],
    )


def run_job(config: AppConfig, *, job_name: str, dry_run: bool = False) -> JobRunResult:
    if job_name == "process_extraction_queue":
        return _process_extraction_queue_job(config, dry_run=dry_run)
    if job_name == "process_promotion_queue":
        return _process_promotion_queue_job(config, dry_run=dry_run)
    if job_name == "expire_stale_episodes":
        return _expire_stale_episodes_job(config, dry_run=dry_run)
    if job_name == "embed_backfill":
        return _embed_backfill(config, dry_run=dry_run)
    if job_name == "status_report":
        return _status_report_job(config, dry_run=dry_run)

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    stats: dict[str, Any] = {}
    status = "ok"

    if job_name == "sync_vault_incremental":
        connection.close()
        sync_result = sync_vault(config, mode="incremental")
        connection = connect_db(config.paths.db_path)
        with connection:
            job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
            stats["scanned_paths"] = sync_result.scanned_paths
            stats["synced_notes"] = sync_result.synced_notes
            stats["deleted_notes"] = sync_result.deleted_notes
            stats["parse_errors"] = sync_result.parse_errors
            _close_job_run(connection, job_id, status=status, stats=stats)
        connection.close()
        return JobRunResult(job_name=job_name, status=status, stats=stats, review_items_created=[])

    if job_name == "wiki_health_report":
        from .maintenance import compute_wiki_health_stats, detect_semantic_duplicates
        from .ops import load_open_review_items, load_open_relearn_tasks, write_maintenance_overview
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            dedup_pairs = detect_semantic_duplicates(connection, config, dry_run=dry_run)
            open_reviews = load_open_review_items(config)
            open_relearns = load_open_relearn_tasks(connection)
            health = compute_wiki_health_stats(
                connection,
                dedup_pairs=dedup_pairs,
                open_review_count=len(open_reviews),
                open_relearn_count=len(open_relearns),
            )
            stats["wiki_total_notes"] = health.total_notes
            stats["wiki_orphan_notes"] = health.orphan_note_count
            stats["semantic_dedup_candidates"] = len(dedup_pairs)
            if not dry_run:
                write_maintenance_overview(
                    config,
                    stats=stats,
                    review_items=open_reviews,
                    contradiction_rows=[
                        item for item in open_reviews if item.review_type == "contradiction"
                    ],
                    stale_rows=[],
                    relearn_tasks=open_relearns,
                    eval_candidates=[],
                    wiki_health=health,
                )
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            with connection:
                _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
            connection.close()
            raise
        connection.close()
        return JobRunResult(job_name=job_name, status="ok", stats=stats, review_items_created=[])

    if job_name == "wiki_importance_scoring":
        from .maintenance import compute_importance_scores
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            if not dry_run:
                signals = compute_importance_scores(connection)
                stats["importance_notes_scored"] = len(signals)
            else:
                stats["importance_notes_scored"] = 0
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            with connection:
                _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
            connection.close()
            raise
        connection.close()
        return JobRunResult(job_name=job_name, status="ok", stats=stats, review_items_created=[])

    if job_name == "wiki_lifecycle_audit":
        from .maintenance import audit_lifecycle_states
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            audit_result = audit_lifecycle_states(connection, config, dry_run=dry_run)
            stats["notes_scanned"] = audit_result.notes_scanned
            stats["advanced_to_verified"] = audit_result.advanced_to_verified
            stats["advanced_to_canonical"] = audit_result.advanced_to_canonical
            stats["drift_flagged"] = audit_result.drift_flagged
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            with connection:
                _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
            connection.close()
            raise
        connection.close()
        return JobRunResult(job_name=job_name, status="ok", stats=stats, review_items_created=[])

    if job_name == "wiki_graph_rebuild":
        from .maintenance import rebuild_wiki_graph
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            graph_result = rebuild_wiki_graph(connection, config, dry_run=dry_run)
            stats["notes_scanned"] = graph_result.notes_scanned
            stats["notes_rebuilt"] = graph_result.notes_rebuilt
            stats["stale_edges_pruned"] = graph_result.stale_edges_pruned
            stats["orphan_nodes_pruned"] = graph_result.orphan_nodes_pruned
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            with connection:
                _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
            connection.close()
            raise
        connection.close()
        return JobRunResult(job_name=job_name, status="ok", stats=stats, review_items_created=[])

    if job_name == "wiki_raw_revisitation":
        from .maintenance import run_raw_revisitation
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            rv_result = run_raw_revisitation(connection, config, dry_run=dry_run)
            stats["sources_requeued"] = rv_result.sources_requeued
            stats["sources_skipped"] = rv_result.sources_skipped
            stats["context_injected_count"] = rv_result.context_injected_count
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            with connection:
                _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
            connection.close()
            raise
        connection.close()
        return JobRunResult(job_name=job_name, status="ok", stats=stats, review_items_created=[])

    if job_name == "wiki_cluster_validation":
        from .maintenance import run_cluster_validation
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            cv_result = run_cluster_validation(connection, config, llm_fn=None, dry_run=dry_run)
            stats["clusters_processed"] = cv_result.clusters_processed
            stats["links_confirmed"] = cv_result.links_confirmed
            stats["links_removed"] = cv_result.links_removed
            stats["merge_reviews_created"] = cv_result.merge_reviews_created
            stats["patterns_named"] = cv_result.patterns_named
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            with connection:
                _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
            connection.close()
            raise
        connection.close()
        return JobRunResult(job_name=job_name, status="ok", stats=stats, review_items_created=[])

    if job_name == "wiki_pattern_discovery":
        from .maintenance import run_pattern_discovery
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            pd_result = run_pattern_discovery(connection, config, llm_fn=None, dry_run=dry_run)
            stats["channel_2_candidates"] = pd_result.channel_2_candidates
            stats["channel_3_candidates"] = pd_result.channel_3_candidates
            stats["archetypes_confirmed"] = pd_result.archetypes_confirmed
            stats["archetypes_rejected"] = pd_result.archetypes_rejected
            stats["edges_updated"] = pd_result.edges_updated
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            with connection:
                _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
            connection.close()
            raise
        connection.close()
        return JobRunResult(job_name=job_name, status="ok", stats=stats, review_items_created=[])

    if job_name == "wiki_embedding_neighbor_backfill":
        from .maintenance import run_embedding_neighbor_backfill
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            links = run_embedding_neighbor_backfill(connection, config)
            stats["neighbor_links_asserted"] = links
            with connection:
                _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            with connection:
                _close_job_run(connection, job_id, status="error", stats=stats, error_text=str(exc))
            connection.close()
            raise
        connection.close()
        return JobRunResult(job_name=job_name, status="ok", stats=stats, review_items_created=[])

    connection.close()
    raise ValueError(f"unsupported job: {job_name}")

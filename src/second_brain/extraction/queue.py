from __future__ import annotations

import json
import sqlite3
from typing import Any

from second_brain.config import AppConfig
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import _migrations_dir
from second_brain.projection.pipeline_models import SourceRef

from .models import ExtractionQueueItem


def enqueue_extraction(
    config: AppConfig,
    *,
    source_kind: str,
    archive_id: str | None = None,
    conversation_id: str | None = None,
    episode_id: str | None = None,
    bridge_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    content_hash: str | None = None,
) -> ExtractionQueueItem:
    _validate_source(source_kind, archive_id=archive_id, conversation_id=conversation_id, episode_id=episode_id)
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO extraction_queue(
                    source_kind, archive_id, conversation_id, episode_id,
                    bridge_id, session_id, task_id, status, content_hash, last_error
                )
                VALUES (?, ?, ?, ?, COALESCE(?, 'job:backfill'), ?, ?, 'pending', ?, NULL)
                ON CONFLICT DO UPDATE SET
                    bridge_id    = COALESCE(excluded.bridge_id, extraction_queue.bridge_id),
                    session_id   = COALESCE(excluded.session_id, extraction_queue.session_id),
                    task_id      = COALESCE(excluded.task_id, extraction_queue.task_id),
                    content_hash = COALESCE(excluded.content_hash, extraction_queue.content_hash),
                    status = CASE
                        WHEN extraction_queue.status = 'processing'
                            THEN extraction_queue.status
                        WHEN excluded.content_hash IS NOT NULL
                         AND extraction_queue.content_hash IS NOT NULL
                         AND excluded.content_hash != extraction_queue.content_hash
                            THEN 'pending'
                        WHEN extraction_queue.status = 'processed'
                            THEN extraction_queue.status
                        ELSE 'pending'
                    END,
                    last_error = NULL
                """,
                (source_kind, archive_id, conversation_id, episode_id, bridge_id, session_id, task_id, content_hash),
            )
        row = _select_queue_row(
            connection,
            source_kind=source_kind,
            archive_id=archive_id,
            conversation_id=conversation_id,
            episode_id=episode_id,
        )
        if row is None:
            raise RuntimeError("failed to read queued extraction row")
        return _row_to_item(row)
    finally:
        connection.close()


def extraction_status(
    config: AppConfig,
    *,
    queue_id: int | None = None,
    errors_only: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    try:
        if queue_id is not None:
            row = connection.execute(
                """
                SELECT q.*,
                       (SELECT COUNT(*) FROM extraction_candidates c WHERE c.queue_id = q.id) AS candidate_rows,
                       (SELECT COUNT(*) FROM extraction_visual_snippets v WHERE v.queue_id = q.id) AS snippet_rows
                FROM extraction_queue q
                WHERE q.id = ?
                """,
                (queue_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"extraction queue row not found: {queue_id}")
            return {"item": _status_row_to_dict(row)}
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM extraction_queue
            GROUP BY status
            """
        ).fetchall()
        oldest = connection.execute(
            """
            SELECT queued_at
            FROM extraction_queue
            WHERE status = 'pending'
            ORDER BY queued_at ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        result: dict[str, Any] = {
            "queue_counts": counts,
            "pending_count": counts.get("pending", 0),
            "error_count": counts.get("error", 0),
            "oldest_pending_queued_at": str(oldest["queued_at"]) if oldest is not None else None,
        }
        if errors_only:
            error_rows = connection.execute(
                """
                SELECT q.*,
                       (SELECT COUNT(*) FROM extraction_candidates c WHERE c.queue_id = q.id) AS candidate_rows,
                       (SELECT COUNT(*) FROM extraction_visual_snippets v WHERE v.queue_id = q.id) AS snippet_rows
                FROM extraction_queue q
                WHERE q.status = 'error'
                ORDER BY q.finished_at DESC NULLS LAST, q.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            result["errors"] = [_status_row_to_dict(row) for row in error_rows]
        else:
            recent = connection.execute(
                """
                SELECT q.*,
                       (SELECT COUNT(*) FROM extraction_candidates c WHERE c.queue_id = q.id) AS candidate_rows,
                       (SELECT COUNT(*) FROM extraction_visual_snippets v WHERE v.queue_id = q.id) AS snippet_rows
                FROM extraction_queue q
                ORDER BY q.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            result["recent"] = [_status_row_to_dict(row) for row in recent]
        return result
    finally:
        connection.close()


def pending_items(connection: sqlite3.Connection, *, limit: int) -> list[ExtractionQueueItem]:
    rows = connection.execute(
        """
        SELECT *
        FROM extraction_queue
        WHERE status = 'pending'
        ORDER BY queued_at ASC, id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def mark_processing(connection: sqlite3.Connection, queue_id: int) -> None:
    connection.execute(
        """
        UPDATE extraction_queue
        SET status = 'processing',
            attempt_count = attempt_count + 1,
            started_at = datetime('now'),
            finished_at = NULL,
            last_error = NULL
        WHERE id = ?
        """,
        (queue_id,),
    )


CURRENT_SCHEMA_VERSION = "1.0"


def mark_finished(
    connection: sqlite3.Connection,
    *,
    queue_id: int,
    status: str,
    duplicate_state: str | None,
    projection_metadata: dict[str, Any],
    window_count: int,
    artifact_count: int,
    visual_snippet_count: int,
    candidate_count: int,
    validation_error_count: int,
    intra_source_relations: list[dict[str, Any]] | None = None,
    extraction_schema_version: str | None = None,
    extraction_model_version: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE extraction_queue
        SET status = ?,
            duplicate_state = ?,
            projection_metadata_json = ?,
            window_count = ?,
            artifact_count = ?,
            visual_snippet_count = ?,
            candidate_count = ?,
            validation_error_count = ?,
            intra_source_relations_json = ?,
            finished_at = datetime('now'),
            extraction_schema_version = COALESCE(?, extraction_schema_version),
            extraction_model_version = COALESCE(?, extraction_model_version),
            last_error = NULL
        WHERE id = ?
        """,
        (
            status,
            duplicate_state,
            json.dumps(projection_metadata, sort_keys=True),
            window_count,
            artifact_count,
            visual_snippet_count,
            candidate_count,
            validation_error_count,
            json.dumps(intra_source_relations or [], sort_keys=True),
            extraction_schema_version or CURRENT_SCHEMA_VERSION,
            extraction_model_version,
            queue_id,
        ),
    )


def mark_error(connection: sqlite3.Connection, *, queue_id: int, error: str) -> None:
    connection.execute(
        """
        UPDATE extraction_queue
        SET status = 'error',
            finished_at = datetime('now'),
            last_error = ?
        WHERE id = ?
        """,
        (error, queue_id),
    )


def queue_row_to_source_ref(item: ExtractionQueueItem) -> SourceRef:
    if item.source_kind in {"artifact", "session"}:
        return SourceRef(item.source_kind, archive_id=item.archive_id)
    return SourceRef(item.source_kind, conversation_id=item.conversation_id, episode_id=item.episode_id)


def source_ref_id(item: ExtractionQueueItem) -> str:
    if item.archive_id:
        return item.archive_id
    return f"episode/{item.conversation_id}/{item.episode_id}"


def _validate_source(
    source_kind: str,
    *,
    archive_id: str | None,
    conversation_id: str | None,
    episode_id: str | None,
) -> None:
    if source_kind not in {"artifact", "episode", "session"}:
        raise ValueError(f"unsupported extraction source kind: {source_kind}")
    if source_kind in {"artifact", "session"}:
        if not archive_id:
            raise ValueError(f"{source_kind} extraction requires archive_id")
        if conversation_id or episode_id:
            raise ValueError(f"{source_kind} extraction does not accept conversation_id/episode_id")
        return
    if not conversation_id or not episode_id:
        raise ValueError("episode extraction requires conversation_id and episode_id")
    if archive_id:
        raise ValueError("episode extraction does not accept archive_id")


def _select_queue_row(
    connection: sqlite3.Connection,
    *,
    source_kind: str,
    archive_id: str | None,
    conversation_id: str | None,
    episode_id: str | None,
) -> sqlite3.Row | None:
    if archive_id is not None:
        return connection.execute(
            """
            SELECT * FROM extraction_queue
            WHERE source_kind = ? AND archive_id = ? AND revisit_of_queue_id IS NULL
            """,
            (source_kind, archive_id),
        ).fetchone()
    return connection.execute(
        """
        SELECT * FROM extraction_queue
        WHERE source_kind = ? AND conversation_id = ? AND episode_id = ?
          AND revisit_of_queue_id IS NULL
        """,
        (source_kind, conversation_id, episode_id),
    ).fetchone()


def _row_to_item(row: sqlite3.Row) -> ExtractionQueueItem:
    return ExtractionQueueItem(
        id=int(row["id"]),
        source_kind=str(row["source_kind"]),
        archive_id=str(row["archive_id"]) if row["archive_id"] is not None else None,
        conversation_id=str(row["conversation_id"]) if row["conversation_id"] is not None else None,
        episode_id=str(row["episode_id"]) if row["episode_id"] is not None else None,
        bridge_id=str(row["bridge_id"]) if row["bridge_id"] is not None else None,
        session_id=str(row["session_id"]) if row["session_id"] is not None else None,
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        duplicate_state=str(row["duplicate_state"]) if row["duplicate_state"] is not None else None,
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
        content_hash=str(row["content_hash"]) if row["content_hash"] is not None else None,
        revisit_of_queue_id=int(row["revisit_of_queue_id"]) if row["revisit_of_queue_id"] is not None else None,
        revisit_reason=str(row["revisit_reason"]) if row["revisit_reason"] is not None else None,
        revisit_context_json=str(row["revisit_context_json"]) if row["revisit_context_json"] is not None else None,
    )


def _status_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key in ("projection_metadata_json",):
        value = payload.get(key)
        payload[key.removesuffix("_json")] = json.loads(str(value or "{}"))
        payload.pop(key, None)
    return payload

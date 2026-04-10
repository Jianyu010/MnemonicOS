from __future__ import annotations

import json

from .config import AppConfig
from .db import connect_db, run_migrations
from .models import JobRunResult
from .ops import load_open_review_items, refresh_active_file, sync_eval_candidates
from .review import create_review_item, slugify
from .sync import sync_vault


def _open_job_run(connection, job_name: str, mode: str) -> int:
    cursor = connection.execute(
        "INSERT INTO job_runs(job_name, mode, status) VALUES (?, ?, 'started')",
        (job_name, mode),
    )
    return int(cursor.lastrowid)


def _close_job_run(connection, job_id: int, *, status: str, stats: dict[str, int], error_text: str | None = None) -> None:
    connection.execute(
        """
        UPDATE job_runs
        SET status = ?, finished_at = datetime('now'), stats_json = ?, error_text = ?
        WHERE id = ?
        """,
        (status, json.dumps(stats), error_text, job_id),
    )


def _retrieval_miss_rows(connection) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            rq.id,
            rq.query,
            rq.query_type,
            rq.created_at,
            rq.useful,
            rq.top1_correct,
            rq.retrieved,
            COUNT(DISTINCT rh.note_id) AS note_hits,
            COUNT(DISTINCT rah.chunk_id) AS archive_hits
        FROM retrieval_queries rq
        LEFT JOIN retrieval_hits rh ON rh.query_id = rq.id
        LEFT JOIN retrieval_archive_hits rah ON rah.query_id = rq.id
        GROUP BY rq.id
        ORDER BY rq.id ASC
        """
    ).fetchall()

    candidates: list[dict[str, object]] = []
    for row in rows:
        reasons: list[str] = []
        note_hits = int(row["note_hits"] or 0)
        archive_hits = int(row["archive_hits"] or 0)
        useful = row["useful"]
        top1_correct = row["top1_correct"]
        observed_hits = json.loads(str(row["retrieved"] or "[]"))

        if useful == 0:
            reasons.append("user_marked_not_useful")
        if top1_correct == 0:
            reasons.append("top1_incorrect")
        if note_hits == 0 and archive_hits > 0:
            reasons.append("archive_only")
        if note_hits == 0 and archive_hits == 0:
            reasons.append("no_results")

        if not reasons:
            continue

        candidates.append(
            {
                "query_id": int(row["id"]),
                "query": str(row["query"]),
                "query_type": str(row["query_type"] or "").strip() or None,
                "created_at": str(row["created_at"] or ""),
                "reasons": reasons,
                "observed_hits": observed_hits,
            }
        )
    return candidates


def run_job(config: AppConfig, *, job_name: str, dry_run: bool = False) -> JobRunResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    review_items: list[str] = []
    stats: dict[str, int] = {}
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

    with connection:
        job_id = _open_job_run(connection, job_name, "dry-run" if dry_run else "live")
        try:
            if job_name not in {"daily_consolidation", "weekly_hygiene"}:
                raise ValueError(f"unsupported job: {job_name}")

            duplicate_alias_rows = connection.execute(
                """
                SELECT alias, GROUP_CONCAT(note_id) AS note_ids, COUNT(DISTINCT note_id) AS note_count
                FROM aliases
                GROUP BY lower(alias)
                HAVING COUNT(DISTINCT note_id) > 1
                ORDER BY note_count DESC, alias ASC
                """
            ).fetchall()

            stale_rows = connection.execute(
                """
                SELECT
                    id,
                    title,
                    COALESCE(last_verified_at, updated_at) AS last_verified_at
                FROM notes
                WHERE type IN ('decision', 'procedure')
                  AND status = 'active'
                  AND COALESCE(last_verified_at, updated_at) < date('now', '-90 days')
                ORDER BY updated_at ASC
                """
            ).fetchall()

            eval_candidate_sync = sync_eval_candidates(
                config,
                _retrieval_miss_rows(connection),
                dry_run=dry_run,
            )

            stats["duplicate_aliases"] = len(duplicate_alias_rows)
            stats["stale_active_notes"] = len(stale_rows)
            stats["eval_candidates_added"] = eval_candidate_sync.added
            stats["open_eval_candidates"] = len(eval_candidate_sync.open_candidates)

            if not dry_run:
                for row in duplicate_alias_rows:
                    alias = str(row["alias"])
                    note_ids = str(row["note_ids"]).split(",")
                    review_path = create_review_item(
                        config,
                        review_type="merge_review",
                        suggested_action=f"merge alias collision for {alias}",
                        reason=f"Alias '{alias}' maps to multiple notes",
                        confidence="medium",
                        agent="job:daily-consolidation",
                        source_refs=[],
                        context=f"Duplicate alias detected for: {', '.join(note_ids)}",
                        candidates=note_ids,
                        slug_seed=f"duplicate-alias-{slugify(alias)}",
                    )
                    review_items.append(f"review/{review_path.stem}")

                for row in stale_rows:
                    note_id = str(row["id"])
                    review_path = create_review_item(
                        config,
                        review_type="verification_review",
                        suggested_action=f"re-verify {note_id}",
                        reason=f"Active note '{row['title']}' is stale",
                        confidence="medium",
                        agent="job:daily-consolidation",
                        source_refs=[note_id],
                        context=f"{note_id} has not been re-verified in over 90 days.",
                        candidates=[note_id],
                        slug_seed=f"stale-{note_id.replace('/', '-')}",
                    )
                    review_items.append(f"review/{review_path.stem}")

                review_summaries = load_open_review_items(config)
                stale_summaries = [
                    {
                        "id": str(row["id"]),
                        "title": str(row["title"]),
                        "last_verified_at": str(row["last_verified_at"]),
                    }
                    for row in stale_rows
                ]
                refresh_active_file(
                    config,
                    review_items=review_summaries,
                    stale_rows=stale_summaries,
                    eval_candidates=eval_candidate_sync.open_candidates,
                )

            stats["open_review_items"] = len(load_open_review_items(config))

            _close_job_run(connection, job_id, status="ok", stats=stats)
        except Exception as exc:
            status = "error"
            _close_job_run(connection, job_id, status=status, stats=stats, error_text=str(exc))
            raise

    connection.close()
    return JobRunResult(
        job_name=job_name,
        status=status,
        stats=stats,
        review_items_created=review_items,
    )

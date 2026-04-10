from __future__ import annotations

import json
from typing import Any

from .config import AppConfig
from .db import connect_db, run_migrations
from .models import JobRunResult
from .ops import (
    load_open_relearn_tasks,
    load_open_review_items,
    refresh_active_file,
    sync_eval_candidates,
    write_maintenance_overview,
)
from .review import create_review_item, slugify
from .sync import sync_vault
from .trust import compute_note_freshness, compute_note_trust_stats, train_rerank_model


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

        if reasons:
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


def _load_active_notes(connection, note_type: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, title, status, updated_at, created_at, last_verified_at, verified_by, entities
        FROM notes
        WHERE type = ? AND status = 'active'
        ORDER BY updated_at DESC, title ASC
        """,
        (note_type,),
    ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "status": str(row["status"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "created_at": str(row["created_at"] or ""),
            "last_verified_at": str(row["last_verified_at"] or ""),
            "verified_by": str(row["verified_by"] or ""),
            "entities": json.loads(str(row["entities"] or "[]")),
        }
        for row in rows
    ]


def _shared_subject(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_entities = {item for item in left["entities"] if not str(item).startswith("person/")}
    right_entities = {item for item in right["entities"] if not str(item).startswith("person/")}
    if left_entities and right_entities and left_entities & right_entities:
        return True
    title_terms_left = {term for term in left["title"].casefold().split() if len(term) > 3}
    title_terms_right = {term for term in right["title"].casefold().split() if len(term) > 3}
    return bool(title_terms_left & title_terms_right)


def _decision_contradictions(connection) -> list[dict[str, Any]]:
    decisions = _load_active_notes(connection, "decision")
    contradictions: list[dict[str, Any]] = []
    for index, left in enumerate(decisions):
        for right in decisions[index + 1 :]:
            if left["id"] == right["id"]:
                continue
            if not _shared_subject(left, right):
                continue
            contradictions.append(
                {
                    "notes": [left["id"], right["id"]],
                    "reason": f"Active decisions '{left['title']}' and '{right['title']}' appear to cover the same subject.",
                    "context": f"Shared subject inferred from overlapping entities/titles: {left['id']}, {right['id']}",
                }
            )
    return contradictions


def _claim_contradictions(connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT note_id, attribute, GROUP_CONCAT(DISTINCT value) AS value_list, COUNT(DISTINCT value) AS value_count
        FROM claims
        WHERE attribute IS NOT NULL
          AND (valid_to IS NULL OR valid_to > date('now'))
        GROUP BY note_id, attribute
        HAVING COUNT(DISTINCT value) > 1
        """
    ).fetchall()
    contradictions: list[dict[str, Any]] = []
    for row in rows:
        values = [value for value in str(row["value_list"] or "").split(",") if value]
        contradictions.append(
            {
                "notes": [str(row["note_id"])],
                "reason": f"Active claims disagree on attribute '{row['attribute']}'.",
                "context": f"Conflicting values for {row['note_id']}: {', '.join(values)}",
            }
        )
    return contradictions


def _procedure_conflicts(connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            p.id AS procedure_id,
            p.title AS procedure_title,
            related.id AS related_id,
            related.title AS related_title,
            related.type AS related_type,
            related.updated_at AS related_updated_at
        FROM notes p
        JOIN graph_edges ge ON ge.target_id = p.id
        JOIN notes related ON related.id = ge.source_note_id
        WHERE p.type = 'procedure'
          AND p.status = 'active'
          AND related.type IN ('incident', 'decision')
          AND related.updated_at > COALESCE(p.last_verified_at, p.updated_at)
          AND ge.relation_type IN ('linked_procedure', 'linked_decision')
        ORDER BY related.updated_at DESC
        """
    ).fetchall()
    conflicts: list[dict[str, Any]] = []
    for row in rows:
        conflicts.append(
            {
                "notes": [str(row["procedure_id"]), str(row["related_id"])],
                "reason": f"Active procedure '{row['procedure_title']}' may conflict with newer {row['related_type']} '{row['related_title']}'.",
                "context": f"{row['related_id']} is newer than the procedure's last verification timestamp.",
            }
        )
    return conflicts


def _verification_debt_rows(connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT id, title, type
        FROM notes
        WHERE type IN ('decision', 'procedure')
          AND status = 'active'
          AND (last_verified_at IS NULL OR verified_by IS NULL OR trim(verified_by) = '')
        ORDER BY updated_at DESC
        """
    ).fetchall()
    return [{"id": str(row["id"]), "title": str(row["title"]), "type": str(row["type"])} for row in rows]


def _upsert_relearn_task(
    connection,
    *,
    note_id: str,
    stage: str,
    reason: str,
    signals: dict[str, Any],
    suggested_evidence: list[str],
    expected_output: str,
) -> str:
    task_id = f"task/{stage}/{slugify(note_id)}"
    connection.execute(
        """
        INSERT INTO relearn_tasks(task_id, note_id, stage, reason, signals_json, suggested_evidence, expected_output, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(task_id) DO UPDATE SET
            reason = excluded.reason,
            signals_json = excluded.signals_json,
            suggested_evidence = excluded.suggested_evidence,
            expected_output = excluded.expected_output,
            updated_at = excluded.updated_at
        """,
        (
            task_id,
            note_id,
            stage,
            reason,
            json.dumps(signals, sort_keys=True),
            json.dumps(suggested_evidence),
            expected_output,
        ),
    )
    return task_id


def _create_contradiction_reviews(config: AppConfig, contradictions: list[dict[str, Any]], *, dry_run: bool) -> list[str]:
    review_items: list[str] = []
    if dry_run:
        return review_items
    for item in contradictions:
        slug_seed = f"contradiction-{'-'.join(note_id.replace('/', '-') for note_id in item['notes'])}"
        review_path = create_review_item(
            config,
            review_type="contradiction_review",
            suggested_action="resolve contradictory truth and supersede stale note if needed",
            reason=item["reason"],
            confidence="medium",
            agent="job:weekly-hygiene",
            source_refs=item["notes"],
            context=item["context"],
            candidates=item["notes"],
            slug_seed=slug_seed,
            extra_metadata={"severity": 3},
        )
        review_items.append(f"review/{review_path.stem}")
    return review_items


def _create_verification_reviews(config: AppConfig, rows: list[dict[str, str]], *, dry_run: bool, agent_name: str) -> list[str]:
    review_items: list[str] = []
    if dry_run:
        return review_items
    for row in rows:
        review_path = create_review_item(
            config,
            review_type="verification_review",
            suggested_action=f"add verification metadata for {row['id']}",
            reason=f"Active {row['type']} '{row['title']}' is missing verification metadata",
            confidence="medium",
            agent=agent_name,
            source_refs=[row["id"]],
            context=f"{row['id']} needs last_verified_at and verified_by to remain trusted guidance.",
            candidates=[row["id"]],
            slug_seed=f"verification-debt-{row['id'].replace('/', '-')}",
            extra_metadata={"severity": 2},
        )
        review_items.append(f"review/{review_path.stem}")
    return review_items


def _create_stale_reviews(config: AppConfig, rows: list[dict[str, str]], *, dry_run: bool, agent_name: str) -> list[str]:
    review_items: list[str] = []
    if dry_run:
        return review_items
    for row in rows:
        review_path = create_review_item(
            config,
            review_type="verification_review",
            suggested_action=f"re-verify {row['id']}",
            reason=f"Note '{row['title']}' is {row.get('freshness_state', 'stale')}",
            confidence="medium",
            agent=agent_name,
            source_refs=[row["id"]],
            context=f"{row['id']} is currently marked {row.get('freshness_state', 'stale')} and should be refreshed before guiding current decisions.",
            candidates=[row["id"]],
            slug_seed=f"stale-{row['id'].replace('/', '-')}",
            extra_metadata={"severity": 2},
        )
        review_items.append(f"review/{review_path.stem}")
    return review_items


def _stale_rows_from_freshness(connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT n.id, n.title, COALESCE(n.last_verified_at, n.updated_at) AS last_verified_at, nf.freshness_state
        FROM note_freshness nf
        JOIN notes n ON n.id = nf.note_id
        WHERE nf.freshness_state IN ('suspect', 'stale', 'contested')
        ORDER BY nf.staleness_score DESC, n.updated_at ASC
        """
    ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "last_verified_at": str(row["last_verified_at"]),
            "freshness_state": str(row["freshness_state"]),
        }
        for row in rows
    ]


def run_job(config: AppConfig, *, job_name: str, dry_run: bool = False) -> JobRunResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    review_items_created: list[str] = []
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

            eval_candidate_sync = sync_eval_candidates(config, _retrieval_miss_rows(connection), dry_run=dry_run)
            trust_signals = compute_note_trust_stats(connection)
            verification_debt_rows = _verification_debt_rows(connection)
            contradictions = _decision_contradictions(connection) + _claim_contradictions(connection) + _procedure_conflicts(connection)

            contradiction_counts: dict[str, int] = {}
            for item in contradictions:
                for note_id in item["notes"]:
                    contradiction_counts[note_id] = contradiction_counts.get(note_id, 0) + 1

            freshness_signals = compute_note_freshness(connection, contradiction_counts_override=contradiction_counts)
            stale_rows = _stale_rows_from_freshness(connection)

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
                        extra_metadata={"severity": 1},
                    )
                    review_items_created.append(f"review/{review_path.stem}")

                review_items_created.extend(
                    _create_verification_reviews(
                        config,
                        verification_debt_rows,
                        dry_run=dry_run,
                        agent_name="job:phase4-verification",
                    )
                )
                review_items_created.extend(
                    _create_stale_reviews(
                        config,
                        stale_rows,
                        dry_run=dry_run,
                        agent_name="job:phase4-staleness",
                    )
                )
                if job_name == "weekly_hygiene":
                    review_items_created.extend(_create_contradiction_reviews(config, contradictions, dry_run=dry_run))

                for note_id, signal in freshness_signals.items():
                    if signal.relearn_stage is None:
                        continue
                    _upsert_relearn_task(
                        connection,
                        note_id=note_id,
                        stage=signal.relearn_stage,
                        reason=f"{note_id} is {signal.freshness_state} with staleness_score={signal.staleness_score:.2f}",
                        signals={
                            "staleness_score": signal.staleness_score,
                            "freshness_state": signal.freshness_state,
                            "contradiction_count": signal.contradiction_count,
                            "linked_incident_count": signal.linked_incident_count,
                            "failure_signal_count": signal.failure_signal_count,
                            "miss_signal_count": signal.miss_signal_count,
                            "newer_evidence_count": signal.newer_evidence_count,
                        },
                        suggested_evidence=[note_id, "recent sessions", "linked incidents", "superseding decisions"],
                        expected_output="Confirm current truth, supersede stale truth, split historical/current knowledge, or mark unresolved.",
                    )

            rerank_model = train_rerank_model(connection, config) if job_name == "weekly_hygiene" else None

            open_review_items = load_open_review_items(config)
            contradiction_review_items = [item for item in open_review_items if item.review_type == "contradiction_review"]
            relearn_tasks = load_open_relearn_tasks(connection)

            stats["duplicate_aliases"] = len(duplicate_alias_rows)
            stats["verification_debt"] = len(verification_debt_rows)
            stats["contradiction_reviews"] = len(contradictions)
            stats["stale_active_notes"] = len(stale_rows)
            stats["eval_candidates_added"] = eval_candidate_sync.added
            stats["open_eval_candidates"] = len(eval_candidate_sync.open_candidates)
            stats["open_review_items"] = len(open_review_items)
            stats["open_relearn_tasks"] = len(relearn_tasks)
            stats["trusted_notes"] = len(trust_signals)
            stats["freshness_notes"] = len(freshness_signals)
            if rerank_model is not None:
                stats["rerank_training_samples"] = rerank_model.sample_count
                stats["rerank_fallback"] = 1 if rerank_model.fallback else 0

            if not dry_run:
                refresh_active_file(
                    config,
                    review_items=[item for item in open_review_items if item.review_type != "contradiction_review"],
                    stale_rows=stale_rows,
                    contradiction_rows=contradiction_review_items,
                    relearn_tasks=relearn_tasks,
                    eval_candidates=eval_candidate_sync.open_candidates,
                )
                if job_name == "weekly_hygiene":
                    write_maintenance_overview(
                        config,
                        stats=stats,
                        review_items=open_review_items,
                        contradiction_rows=contradiction_review_items,
                        stale_rows=stale_rows,
                        relearn_tasks=relearn_tasks,
                        eval_candidates=eval_candidate_sync.open_candidates,
                    )

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
        review_items_created=review_items_created,
    )

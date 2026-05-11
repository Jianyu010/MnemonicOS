from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..db import connect_db, run_migrations
from ..parser import NoteParseError, split_frontmatter
from .decisions import decide
from .gate import run_gate
from .models import PromotionRunResult
from .renderer import render
from .writer import write_promotion, write_rejected

if TYPE_CHECKING:
    from ..config import AppConfig

_DEFAULT_BRIDGE_ID = "job:process-promotion-queue"


def process_promotion_queue(
    config: "AppConfig",
    *,
    dry_run: bool = False,
) -> PromotionRunResult:
    """Main entry point for the promotion daemon job.

    Fetches up to max_candidates_per_run promotion_ready candidates,
    runs the gate → decision → render → write pipeline for each.
    """
    result = PromotionRunResult()
    promo_cfg = getattr(config, "promotion", None)
    max_batch = getattr(promo_cfg, "max_candidates_per_run", 20) if promo_cfg is not None else 20
    retry_hours = getattr(promo_cfg, "retry_after_hours", 24) if promo_cfg is not None else 24
    pre_relation_sync_done = False

    conn = connect_db(config.paths.db_path)
    run_migrations(conn, config.paths.workspace_root / "migrations")

    try:
        # Requeue stale rejected candidates
        if dry_run:
            count_row = conn.execute(
                """
                SELECT COUNT(*) FROM promotion_decisions
                WHERE promotion_state = 'rejected'
                  AND created_at < datetime('now', '-' || ? || ' hours')
                """,
                (retry_hours,),
            ).fetchone()
            result.requeued = int(count_row[0])
        else:
            with conn:
                conn.execute(
                    """
                    UPDATE extraction_candidates
                    SET promotion_state = 'promotion_ready'
                    WHERE id IN (
                        SELECT candidate_id FROM promotion_decisions
                        WHERE promotion_state = 'rejected'
                          AND created_at < datetime('now', '-' || ? || ' hours')
                    )
                    """,
                    (retry_hours,),
                )
                cur = conn.execute(
                    """
                    DELETE FROM promotion_decisions
                    WHERE promotion_state = 'rejected'
                      AND created_at < datetime('now', '-' || ? || ' hours')
                    """,
                    (retry_hours,),
                )
                result.requeued = cur.rowcount

        # Fetch candidates that are promotion_ready and not yet decided
        rows = conn.execute(
            """
            SELECT
                ec.id AS candidate_id,
                ec.queue_id,
                ec.candidate_index,
                ec.candidate_type,
                ec.title,
                ec.payload_json,
                eq.archive_id,
                eq.duplicate_state,
                eq.bridge_id AS source_bridge_id
            FROM extraction_candidates ec
            JOIN extraction_queue eq ON eq.id = ec.queue_id
            WHERE ec.promotion_state = 'promotion_ready'
              AND NOT EXISTS (
                  SELECT 1 FROM promotion_decisions pd WHERE pd.candidate_id = ec.id
              )
            ORDER BY ec.id ASC
            LIMIT ?
            """,
            (max_batch,),
        ).fetchall()

        # queue_id → {candidate_index → note_id} for successfully promoted candidates
        promoted_by_queue: dict[int, dict[int, str]] = {}

        for row in rows:
            candidate_id = int(row["candidate_id"])
            queue_id = int(row["queue_id"])
            candidate_index = int(row["candidate_index"])
            archive_id = str(row["archive_id"] or "")
            duplicate_state = row["duplicate_state"]

            try:
                payload = json.loads(str(row["payload_json"]))
            except (json.JSONDecodeError, TypeError) as exc:
                result.errors.append(f"candidate:{candidate_id}:json_error:{exc}")
                result.rejected += 1
                continue

            # Phase 1: Gate
            gate = run_gate(payload, config=config)
            if not gate.ok:
                if not dry_run:
                    with conn:
                        write_rejected(conn, candidate_id, gate.errors, _DEFAULT_BRIDGE_ID)
                result.rejected += 1
                continue

            # Phase 2: Decision
            try:
                decision = decide(
                    payload,
                    duplicate_state=duplicate_state,
                    conn=conn,
                    config=config,
                )
            except Exception as exc:
                result.errors.append(f"candidate:{candidate_id}:decision_error:{exc}")
                result.rejected += 1
                continue

            if dry_run:
                # Dry-run: report decision without writing
                _report_dry_run(candidate_id, decision)
                if decision.decision == "review":
                    result.review_routed += 1
                else:
                    result.promoted += 1
                continue

            # Phase 3: Render
            existing_path = _resolve_existing_path(decision, config)
            try:
                metadata, rendered_markdown = render(
                    payload,
                    decision=decision,
                    candidate_id=candidate_id,
                    archive_id=archive_id,
                    existing_note_path=existing_path,
                )
            except Exception as exc:
                result.errors.append(f"candidate:{candidate_id}:render_error:{exc}")
                result.rejected += 1
                continue

            # Phase 4: Write
            try:
                with conn:
                    write_promotion(
                        rendered_markdown,
                        metadata=metadata,
                        decision=decision,
                        candidate_id=candidate_id,
                        archive_id=archive_id,
                        bridge_id=_DEFAULT_BRIDGE_ID,
                        conn=conn,
                        vault_root=config.paths.vault_root,
                    )
            except Exception as exc:
                result.errors.append(f"candidate:{candidate_id}:write_error:{exc}")
                result.rejected += 1
                continue

            if decision.decision == "review":
                result.review_routed += 1
            else:
                result.promoted += 1
                # Record for intra-source relation second pass (keyed by candidate_index)
                pd_row = conn.execute(
                    "SELECT target_note_id FROM promotion_decisions WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
                if pd_row is not None:
                    note_id = str(pd_row["target_note_id"] or "")
                    if note_id:
                        promoted_by_queue.setdefault(queue_id, {})[candidate_index] = note_id

        if not dry_run and promoted_by_queue:
            # Newly promoted files must be present in notes before graph_edges can
            # satisfy their foreign key to notes.id during relation rebuild.
            from ..sync import sync_vault
            sync_vault(config, mode="incremental")
            pre_relation_sync_done = True
            _apply_intra_source_relations(conn, config, promoted_by_queue, result)
            if config.embeddings.enabled and config.maintenance.embedding_neighbor_enabled:
                from ..maintenance import run_embedding_neighbor_pass
                for queue_map in promoted_by_queue.values():
                    for note_id in queue_map.values():
                        if note_id:
                            result.relations_applied += run_embedding_neighbor_pass(conn, config, note_id)

    finally:
        conn.close()

    if not dry_run and (result.promoted > 0 or result.review_routed > 0):
        from ..sync import sync_vault
        if not pre_relation_sync_done or result.relations_applied > 0:
            sync_vault(config, mode="incremental")
        if result.promoted > 0:
            from ..pipeline import trigger_embed_backfill
            trigger_embed_backfill(config)

    return result


def _resolve_existing_path(decision, config: "AppConfig"):
    """Return the existing note Path for confirm/update decisions, or None."""
    if decision.decision not in {"confirm", "update"} or decision.target_note_id is None:
        return None
    note_id = decision.target_note_id
    if "/" not in note_id:
        return None
    note_type, slug = note_id.split("/", 1)

    from ..ingest import TYPE_TO_DIRECTORY

    if note_type == "procedure":
        procedures_root = config.paths.vault_root / "wiki" / "procedures"
        for subdir in ("active", "drafts", "retired"):
            candidate = procedures_root / subdir / f"{slug}.md"
            if candidate.exists():
                return candidate
        return None

    subdir = TYPE_TO_DIRECTORY.get(note_type)
    if subdir is None:
        return None
    path = config.paths.vault_root / "wiki" / subdir / f"{slug}.md"
    return path if path.exists() else None


def _report_dry_run(candidate_id: int, decision) -> None:
    import sys
    print(
        f"[dry-run] candidate:{candidate_id} → {decision.decision}"
        f" target={decision.target_note_id} reason={decision.reason}",
        file=sys.stderr,
    )


def apply_relation_to_note(note_path: Path, *, target_note_id: str, relation_type: str) -> bool:
    """Append {target, type} to the note's frontmatter `relations` list, idempotently.

    Returns True if a relation was added, False if it was already present or note missing.
    Writes atomically (tmp → os.rename).
    """
    if not note_path.exists():
        return False
    try:
        text = note_path.read_text(encoding="utf-8")
        parsed = split_frontmatter(text)
    except (OSError, NoteParseError):
        return False

    metadata = parsed.metadata
    relations: list[Any] = metadata.get("relations") if isinstance(metadata.get("relations"), list) else []

    # Idempotent: skip if already present
    for entry in relations:
        if isinstance(entry, dict):
            if str(entry.get("target", "")) == target_note_id and str(entry.get("type", "")) == relation_type:
                return False

    relations = list(relations)
    relations.append({"target": target_note_id, "type": relation_type})
    metadata = dict(metadata)
    metadata["relations"] = relations

    new_frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False, default_flow_style=False)
    new_text = f"---\n{new_frontmatter}---\n\n{parsed.body}\n"

    tmp_path = note_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(new_text, encoding="utf-8")
        os.rename(tmp_path, note_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        return False
    return True


def _note_path_for_id(note_id: str, config: "AppConfig") -> Path | None:
    """Resolve a note_id like 'source/my-slug' to an absolute wiki path."""
    if "/" not in note_id:
        return None
    note_type, slug = note_id.split("/", 1)

    from ..ingest import TYPE_TO_DIRECTORY

    if note_type == "procedure":
        procedures_root = config.paths.vault_root / "wiki" / "procedures"
        for subdir in ("active", "drafts", "retired"):
            candidate = procedures_root / subdir / f"{slug}.md"
            if candidate.exists():
                return candidate
        return None

    subdir = TYPE_TO_DIRECTORY.get(note_type)
    if subdir is None:
        return None
    path = config.paths.vault_root / "wiki" / subdir / f"{slug}.md"
    return path if path.exists() else None


def _apply_intra_source_relations(
    conn,
    config: "AppConfig",
    promoted_by_queue: dict[int, dict[int, str]],
    result: PromotionRunResult,
) -> None:
    """Bundle-aware second pass: apply LLM-asserted intra-source relations to wiki frontmatter.

    For each queue row that had promoted candidates, load intra_source_relations_json,
    resolve source_index/target_index to note IDs via promoted_by_queue (keyed by
    candidate_index), append relations to frontmatter, and rebuild graph edges.
    """
    from ..graph import rebuild_note_graph
    from ..parser import parse_note

    queue_ids = list(promoted_by_queue.keys())
    if not queue_ids:
        return

    placeholders = ",".join("?" * len(queue_ids))
    rows = conn.execute(
        f"SELECT id, intra_source_relations_json FROM extraction_queue WHERE id IN ({placeholders})",
        queue_ids,
    ).fetchall()

    for row in rows:
        queue_id = int(row["id"])
        index_to_note_id = promoted_by_queue.get(queue_id, {})
        if not index_to_note_id:
            continue
        try:
            relations: list[dict[str, Any]] = json.loads(str(row["intra_source_relations_json"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(relations, list):
            continue

        updated_note_ids: set[str] = set()
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            rel_type = str(rel.get("relation_type") or "").strip()
            if not rel_type:
                continue

            # Index-based resolution (current format)
            src_raw = rel.get("source_index")
            tgt_raw = rel.get("target_index")
            if src_raw is not None and tgt_raw is not None:
                try:
                    src_note_id = index_to_note_id.get(int(src_raw))
                    tgt_note_id = index_to_note_id.get(int(tgt_raw))
                except (TypeError, ValueError):
                    continue
            else:
                continue

            if not src_note_id or not tgt_note_id:
                continue

            src_path = _note_path_for_id(src_note_id, config)
            if src_path is None:
                continue

            added = apply_relation_to_note(src_path, target_note_id=tgt_note_id, relation_type=rel_type)
            if added:
                result.relations_applied += 1
                updated_note_ids.add(src_note_id)

        # Rebuild graph edges for every note that had a relation appended
        for note_id in updated_note_ids:
            note_path = _note_path_for_id(note_id, config)
            if note_path is None:
                continue
            try:
                note = parse_note(note_path)
                with conn:
                    rebuild_note_graph(conn, note)
            except Exception:
                pass

from __future__ import annotations

from datetime import date, datetime
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import sqlite3

import yaml

from .config import AppConfig
from .graph import rebuild_note_graph
from .parser import NoteParseError, parse_note, split_frontmatter


logger = logging.getLogger(__name__)

_SKIPPED_LIFECYCLE_STATES = {"review", "contested", "archived"}
_SKIPPED_STATUSES = {"retired", "archived", "superseded"}


@dataclass(slots=True)
class GraphRebuildResult:
    notes_scanned: int
    notes_rebuilt: int
    stale_edges_pruned: int
    orphan_nodes_pruned: int


def rebuild_wiki_graph(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    dry_run: bool = False,
) -> GraphRebuildResult:
    rows = connection.execute(
        "SELECT id, body_path FROM notes WHERE status IS NULL OR status NOT IN ('retired', 'archived')"
    ).fetchall()

    notes_scanned = 0
    notes_rebuilt = 0

    parsed_notes = []
    for row in rows:
        notes_scanned += 1
        from pathlib import Path
        path = Path(str(row["body_path"]))
        if not path.exists():
            logger.warning("rebuild_wiki_graph: missing file %s (note %s)", path, row["id"])
            continue
        try:
            note = parse_note(path)
        except NoteParseError as exc:
            logger.warning("rebuild_wiki_graph: parse error %s for %s", exc, path)
            continue
        parsed_notes.append(note)

    if not dry_run:
        with connection:
            for note in parsed_notes:
                rebuild_note_graph(connection, note)
                notes_rebuilt += 1

            stale_edges_cursor = connection.execute(
                "DELETE FROM graph_edges WHERE target_id NOT IN (SELECT id FROM graph_nodes)"
            )
            stale_edges_pruned = stale_edges_cursor.rowcount

            orphan_cursor = connection.execute(
                """
                DELETE FROM graph_nodes
                WHERE node_type = 'entity'
                  AND id NOT IN (SELECT source_note_id FROM graph_edges)
                  AND id NOT IN (SELECT target_id FROM graph_edges)
                """
            )
            orphan_nodes_pruned = orphan_cursor.rowcount
    else:
        notes_rebuilt = len(parsed_notes)
        stale_edges_pruned = 0
        orphan_nodes_pruned = 0

    return GraphRebuildResult(
        notes_scanned=notes_scanned,
        notes_rebuilt=notes_rebuilt,
        stale_edges_pruned=stale_edges_pruned,
        orphan_nodes_pruned=orphan_nodes_pruned,
    )


@dataclass(slots=True)
class LifecycleAuditResult:
    notes_scanned: int
    advanced_to_verified: int
    advanced_to_canonical: int
    drift_flagged: int


def _write_lifecycle_state(path: Path, target_state: str) -> None:
    raw = path.read_text(encoding="utf-8")
    parsed = split_frontmatter(raw)
    metadata = parsed.metadata
    metadata["lifecycle_state"] = target_state
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).strip()
    rendered = f"---\n{frontmatter}\n---\n\n{parsed.body.strip()}\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, path)


def _days_since(created_at: str) -> int:
    try:
        created = datetime.fromisoformat(created_at.split("T")[0]).date()
    except (ValueError, AttributeError):
        return 0
    return (date.today() - created).days


def audit_lifecycle_states(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    dry_run: bool = False,
) -> LifecycleAuditResult:
    m = config.maintenance
    rows = connection.execute(
        """
        SELECT id, body_path, lifecycle_state, corroboration_count, created_at, status
        FROM notes
        WHERE status IS NULL OR status NOT IN ('retired', 'archived', 'superseded')
        """
    ).fetchall()

    notes_scanned = len(rows)
    advanced_to_verified = 0
    advanced_to_canonical = 0
    drift_flagged = 0

    for row in rows:
        note_id = str(row["id"])
        body_path = Path(str(row["body_path"]))
        lifecycle_state = str(row["lifecycle_state"] or "draft").strip()
        corroboration_count = int(row["corroboration_count"] or 0)
        created_at = str(row["created_at"] or "")

        if lifecycle_state in _SKIPPED_LIFECYCLE_STATES:
            continue

        # Advancement pass
        target_state: str | None = None
        if lifecycle_state == "draft" and corroboration_count >= m.lifecycle_canonical_threshold:
            target_state = "canonical"
        elif lifecycle_state == "draft" and corroboration_count >= m.lifecycle_verified_threshold:
            target_state = "verified"
        elif lifecycle_state == "verified" and corroboration_count >= m.lifecycle_canonical_threshold:
            target_state = "canonical"

        if target_state is not None:
            if not dry_run:
                if not body_path.exists():
                    logger.warning("audit_lifecycle_states: missing file %s (note %s)", body_path, note_id)
                else:
                    try:
                        _write_lifecycle_state(body_path, target_state)
                    except Exception as exc:
                        logger.warning("audit_lifecycle_states: write failed for %s: %s", note_id, exc)
                        continue
                    with connection:
                        connection.execute(
                            "UPDATE notes SET lifecycle_state = ? WHERE id = ?",
                            (target_state, note_id),
                        )
            if target_state == "canonical":
                advanced_to_canonical += 1
            else:
                advanced_to_verified += 1
            continue

        # Drift pass — draft with no corroboration and old created_at
        if lifecycle_state == "draft" and corroboration_count == 0:
            if _days_since(created_at) > m.lifecycle_drift_days:
                if not dry_run:
                    from .review import create_review_item
                    create_review_item(
                        config,
                        review_type="lifecycle_review",
                        suggested_action="investigate or archive stalled draft",
                        reason=f"draft note stalled for >{m.lifecycle_drift_days} days with no corroboration",
                        confidence="low",
                        source_refs=[note_id],
                        slug_seed=f"lifecycle-drift-{note_id}",
                    )
                drift_flagged += 1

    return LifecycleAuditResult(
        notes_scanned=notes_scanned,
        advanced_to_verified=advanced_to_verified,
        advanced_to_canonical=advanced_to_canonical,
        drift_flagged=drift_flagged,
    )


def detect_orphan_nodes(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT n.id
        FROM notes n
        WHERE (n.status IS NULL OR n.status NOT IN ('retired', 'archived'))
          AND NOT EXISTS (SELECT 1 FROM graph_edges ge WHERE ge.source_note_id = n.id)
          AND NOT EXISTS (SELECT 1 FROM graph_edges ge WHERE ge.target_id = n.id)
        """
    ).fetchall()
    return [str(row["id"]) for row in rows]

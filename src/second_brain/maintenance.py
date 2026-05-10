from __future__ import annotations

import logging
from dataclasses import dataclass
import sqlite3

from .config import AppConfig
from .graph import rebuild_note_graph
from .parser import NoteParseError, parse_note


logger = logging.getLogger(__name__)


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

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.jobs import run_job
from second_brain.maintenance import GraphRebuildResult, detect_orphan_nodes, rebuild_wiki_graph
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _minimal_config(workspace: Path) -> Path:
    config_path = workspace / "config.toml"
    _write(
        config_path,
        f"""
        [paths]
        vault_root = "{(workspace / 'brain').as_posix()}"
        db_path = "{(workspace / 'brain/data/archive.db').as_posix()}"
        vectors_dir = "{(workspace / 'brain/data/vectors').as_posix()}"
        graph_dir = "{(workspace / 'brain/data/graph').as_posix()}"
        eval_queries = "{(workspace / 'evals/queries.jsonl').as_posix()}"

        [retrieval]
        top_k = 5
        include_archive_fallback = true

        [ingest]
        chunk_tokens = 80
        chunk_overlap_tokens = 10
        default_archive_type = "session"
        explicit_markers_only = false

        [embeddings]
        enabled = false
        provider = "hash"
        model = "hash-128-v1"
        dimensions = 128
        reembed_on_summary_change_only = true
        """,
    )
    return config_path


def _system_files(workspace: Path) -> None:
    for name in ("MEMORY.md", "USER.md", "ACTIVE.md"):
        _write(workspace / "brain/system" / name, f"# {name}\n")
    _write(
        workspace / "brain/system/AGENTS.md",
        """
        bridges:
          local_cli:
            scope: [wiki]
            can_promote: false
            can_write_canonical: true
        """,
    )


def _note(workspace: Path, rel_path: str, content: str) -> None:
    _write(workspace / "brain/wiki" / rel_path, content)


class GraphRebuildTest(unittest.TestCase):
    def _setup(self, workspace: Path):
        _system_files(workspace)
        config_path = _minimal_config(workspace)
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def test_rebuild_repairs_drift_entity_node(self) -> None:
        """Delete a linked note from DB; rebuild restores the edge via an entity node."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/note-a.md",
                """
                ---
                id: concept/note-a
                type: concept
                title: Note A
                entities: [concept/note-b]
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Body of note A.
                """,
            )
            _note(
                workspace,
                "concepts/active/note-b.md",
                """
                ---
                id: concept/note-b
                type: concept
                title: Note B
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Body of note B.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            # Simulate drift: delete note B from DB (keep file on disk)
            with connection:
                connection.execute("DELETE FROM notes WHERE id = 'concept/note-b'")
                connection.execute("DELETE FROM graph_nodes WHERE id = 'concept/note-b'")
                connection.execute("DELETE FROM graph_edges WHERE source_note_id = 'concept/note-a'")

            result = rebuild_wiki_graph(connection, config)
            connection.close()

            self.assertEqual(result.notes_rebuilt, 1)  # only note-a in DB; note-b was deleted
            connection = connect_db(config.paths.db_path)
            edge = connection.execute(
                "SELECT * FROM graph_edges WHERE source_note_id = 'concept/note-a' AND target_id = 'concept/note-b'"
            ).fetchone()
            # node B re-created as entity node since note was deleted from DB
            node = connection.execute(
                "SELECT node_type FROM graph_nodes WHERE id = 'concept/note-b'"
            ).fetchone()
            connection.close()
            self.assertIsNotNone(edge)
            self.assertIsNotNone(node)
            self.assertEqual(node["node_type"], "entity")

    def test_rebuild_prunes_stale_edges(self) -> None:
        """Stale graph_edges pointing to nonexistent graph_nodes are deleted.

        Uses a retired-status note as source so it is excluded from per-note
        rebuild (which would otherwise delete all its edges first). The explicit
        prune step must fire to remove the stale edge.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/lone.md",
                """
                ---
                id: concept/lone
                type: concept
                title: Lone Note
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Solo note.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            # Retire the note so rebuild_wiki_graph excludes it from per-note rebuild.
            # Then insert a stale edge from it to a ghost target with no graph_node.
            # The explicit prune step must catch it.
            with connection:
                connection.execute("UPDATE notes SET status = 'retired' WHERE id = 'concept/lone'")
                connection.execute(
                    """
                    INSERT INTO graph_edges(source_note_id, target_id, target_kind, relation_type, weight, updated_at)
                    VALUES ('concept/lone', 'ghost/nonexistent', 'concept', 'entity', 0.5, datetime('now'))
                    """
                )

            stale_before = connection.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE target_id = 'ghost/nonexistent'"
            ).fetchone()[0]
            self.assertEqual(stale_before, 1)

            result = rebuild_wiki_graph(connection, config)
            connection.close()

            connection = connect_db(config.paths.db_path)
            stale_after = connection.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE target_id = 'ghost/nonexistent'"
            ).fetchone()[0]
            connection.close()

            self.assertEqual(stale_after, 0)
            self.assertGreaterEqual(result.stale_edges_pruned, 1)

    def test_detect_orphan_nodes(self) -> None:
        """Note with no entity_relations has no edges; detect_orphan_nodes returns its ID."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/island.md",
                """
                ---
                id: concept/island
                type: concept
                title: Island Note
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                No links anywhere.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            rebuild_wiki_graph(connection, config)

            orphans = detect_orphan_nodes(connection)
            connection.close()

            self.assertIn("concept/island", orphans)

    def test_dry_run_no_writes(self) -> None:
        """dry_run=True produces no DB changes."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/stable.md",
                """
                ---
                id: concept/stable
                type: concept
                title: Stable Note
                entities: [concept/other]
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Has a link.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")

            # Capture edge count before dry run
            edges_before = connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
            nodes_before = connection.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]

            # Insert stale edge to verify it is NOT pruned in dry_run
            with connection:
                connection.execute(
                    """
                    INSERT INTO graph_edges(source_note_id, target_id, target_kind, relation_type, weight, updated_at)
                    VALUES ('concept/stable', 'ghost/dry', 'concept', 'entity', 0.5, datetime('now'))
                    """
                )

            result = rebuild_wiki_graph(connection, config, dry_run=True)

            # Stale edge should still be there
            stale = connection.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE target_id = 'ghost/dry'"
            ).fetchone()[0]
            connection.close()

            self.assertEqual(stale, 1)  # not pruned
            self.assertEqual(result.stale_edges_pruned, 0)
            self.assertEqual(result.orphan_nodes_pruned, 0)
            self.assertGreater(result.notes_rebuilt, 0)

    def test_wiki_graph_rebuild_job(self) -> None:
        """wiki_graph_rebuild job runs dry-run without error and includes stats keys."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            _note(
                workspace,
                "concepts/active/test-note.md",
                """
                ---
                id: concept/test-note
                type: concept
                title: Test Note
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Content.
                """,
            )
            sync_vault(config, mode="full")

            result = run_job(config, job_name="wiki_graph_rebuild", dry_run=True)

            self.assertEqual(result.status, "ok")
            self.assertIn("notes_scanned", result.stats)
            self.assertIn("notes_rebuilt", result.stats)
            self.assertIn("stale_edges_pruned", result.stats)
            self.assertIn("orphan_nodes_pruned", result.stats)


if __name__ == "__main__":
    unittest.main()

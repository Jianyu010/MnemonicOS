from __future__ import annotations

from pathlib import Path
import sqlite3
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
from second_brain.retrieval import retrieve
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class Phase3Test(unittest.TestCase):
    def test_graph_expansion_surfaces_related_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)

            _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
            _write(workspace / "brain/system/USER.md", "# USER\n")
            _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
            _write(
                workspace / "brain/system/AGENTS.md",
                """
                agents:
                  codex:
                    scope: [raw/sessions, wiki]
                    can_promote: false
                    can_write_canonical: true
                """,
            )
            _write(
                workspace / "brain/wiki/people/sarah-chen.md",
                """
                ---
                id: person/sarah-chen
                type: person
                title: Sarah Chen
                aliases: [s.chen]
                tags: [auth, leadership]
                updated_at: 2026-04-10
                created_at: 2026-01-15
                source_refs: [session/2026-01-15-1030]
                confidence: high
                role: Staff Engineer
                ---

                Leads the auth platform team.
                """,
            )
            _write(
                workspace / "brain/wiki/decisions/auth-postgres.md",
                """
                ---
                id: decision/auth-postgres
                type: decision
                title: Use Postgres for auth user records
                aliases: [auth-db-choice]
                status: active
                tags: [database, auth]
                updated_at: 2026-04-10
                created_at: 2026-02-12
                last_verified_at: 2026-04-10
                verified_by: human
                source_refs: [session/2026-02-12-1530]
                confidence: high
                owners: [person/sarah-chen]
                ---

                Postgres is the primary store for auth user records and audit logs.
                """,
            )

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

                [embeddings]
                enabled = false

                [graph]
                enabled = true
                focal_limit = 3
                expand_limit = 6
                graph_weight = 0.25
                """,
            )

            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            sync_result = sync_vault(config, mode="full")
            self.assertEqual(sync_result.parse_errors, 0)
            self.assertEqual(sync_result.synced_notes, 2)

            result = retrieve(
                config,
                "Which decision is Sarah Chen responsible for?",
                top_k=3,
                query_type_hint="decision",
            )
            self.assertTrue(result.hits)
            self.assertEqual(result.hits[0].id, "decision/auth-postgres")
            self.assertIn("graph", result.hits[0].channels)

            connection = connect_db(config.paths.db_path)
            edge_count = connection.execute("SELECT COUNT(*) AS count FROM graph_edges").fetchone()
            node_count = connection.execute("SELECT COUNT(*) AS count FROM graph_nodes").fetchone()
            connection.close()
            self.assertEqual(int(edge_count["count"]), 1)
            self.assertGreaterEqual(int(node_count["count"]), 2)


if __name__ == "__main__":
    unittest.main()

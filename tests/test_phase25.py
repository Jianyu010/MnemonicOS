from __future__ import annotations

import json
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
from second_brain.review import create_review_item
from second_brain.retrieval import retrieve
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class Phase25Test(unittest.TestCase):
    def test_daily_consolidation_refreshes_active_and_mines_eval_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)

            _write(
                workspace / "brain/system/MEMORY.md",
                """
                # MEMORY

                ## Agent Behavior
                - Default to archive unless promotion rules clearly apply.
                """,
            )
            _write(
                workspace / "brain/system/USER.md",
                """
                # USER

                ## Preferences
                - Prefer explicit APIs
                """,
            )
            _write(
                workspace / "brain/system/ACTIVE.md",
                """
                # ACTIVE
                updated_at: 2026-04-10

                ## Current Focus
                - Ship the MnemonicOS maintenance loop.

                ## Open Loops
                - [ ] Review the first stale-note report.
                """,
            )
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
                workspace / "brain/wiki/decisions/auth-postgres.md",
                """
                ---
                id: decision/auth-postgres
                type: decision
                title: Use Postgres for auth user records
                aliases: [auth-db]
                status: active
                tags: [database, auth]
                updated_at: 2020-01-10
                created_at: 2020-01-10
                last_verified_at: 2020-01-10
                verified_by: human
                source_refs: [session/2020-01-10-0900]
                confidence: high
                ---

                Postgres stores the auth user records and audit log.
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

            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            sync_result = sync_vault(config, mode="full")
            self.assertEqual(sync_result.parse_errors, 0)
            self.assertEqual(sync_result.synced_notes, 1)

            review_path = create_review_item(
                config,
                review_type="merge_review",
                suggested_action="merge auth-db aliases",
                reason="Auth DB aliases overlap across notes",
                confidence="medium",
                agent="codex",
                source_refs=[],
                context="Manual review seed for ACTIVE.md refresh.",
                candidates=["decision/auth-postgres"],
                slug_seed="manual-review",
            )
            self.assertTrue(review_path.exists())

            miss_query = "zxqvpl rollout checksum protocol"
            miss_result = retrieve(config, miss_query, top_k=3)
            self.assertFalse(miss_result.hits)

            job_result = run_job(config, job_name="daily_consolidation", dry_run=False)
            self.assertEqual(job_result.stats["stale_active_notes"], 1)
            self.assertEqual(job_result.stats["eval_candidates_added"], 1)
            self.assertGreaterEqual(job_result.stats["open_review_items"], 2)

            active_text = (workspace / "brain/system/ACTIVE.md").read_text()
            self.assertIn("## Auto Loops", active_text)
            self.assertIn("Ship the MnemonicOS maintenance loop.", active_text)
            self.assertIn("review/manual-review", active_text)
            self.assertIn("decision/auth-postgres", active_text)
            self.assertIn(miss_query, active_text)

            candidates_path = workspace / "evals/candidates.jsonl"
            self.assertTrue(candidates_path.exists())
            candidate_entries = [json.loads(line) for line in candidates_path.read_text().splitlines() if line.strip()]
            self.assertEqual(len(candidate_entries), 1)
            self.assertEqual(candidate_entries[0]["reason"], "no_results")
            self.assertEqual(candidate_entries[0]["query"], miss_query)


if __name__ == "__main__":
    unittest.main()

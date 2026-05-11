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
from second_brain.retrieval import retrieve
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class Phase4Test(unittest.TestCase):
    def test_retrieval_penalizes_stale_current_truth_but_keeps_history_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
            _write(workspace / "brain/system/USER.md", "# USER\n")
            _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
            _write(
                workspace / "brain/system/AGENTS.md",
                """
                bridges:
                  local_cli:
                    scope: [raw/sessions, wiki]
                    can_promote: false
                    can_write_canonical: true
                """,
            )
            _write(
                workspace / "brain/wiki/concepts/jwt-rotation-current.md",
                """
                ---
                id: concept/jwt-rotation-current
                type: concept
                title: JWT Rotation Strategy
                aliases: [jwt rotation]
                tags: [auth]
                updated_at: 2026-04-10
                created_at: 2026-04-10
                source_refs: []
                confidence: high
                ---

                Current strategy uses rolling keysets with short access token TTLs.
                """,
            )
            _write(
                workspace / "brain/wiki/concepts/jwt-rotation-legacy.md",
                """
                ---
                id: concept/jwt-rotation-legacy
                type: concept
                title: Legacy JWT Rotation Strategy
                aliases: [legacy jwt rotation]
                tags: [auth]
                updated_at: 2020-01-10
                created_at: 2020-01-10
                source_refs: []
                confidence: high
                ---

                Previous legacy strategy used long-lived shared secrets.
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

                [embeddings]
                enabled = false

                [trust]
                current_query_stale_penalty = 0.50
                historical_query_relief = 0.10
                """,
            )
            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute(
                    """
                    INSERT INTO note_freshness(note_id, staleness_score, freshness_state, contradiction_count, linked_incident_count, failure_signal_count, miss_signal_count, superseded_flag, newer_evidence_count, relearn_stage, last_computed_at)
                    VALUES ('concept/jwt-rotation-current', 0.05, 'fresh', 0, 0, 0, 0, 0, 0, NULL, datetime('now'))
                    """
                )
                connection.execute(
                    """
                    INSERT INTO note_freshness(note_id, staleness_score, freshness_state, contradiction_count, linked_incident_count, failure_signal_count, miss_signal_count, superseded_flag, newer_evidence_count, relearn_stage, last_computed_at)
                    VALUES ('concept/jwt-rotation-legacy', 0.85, 'stale', 0, 0, 0, 0, 0, 0, 'targeted_relearn_task', datetime('now'))
                    """
                )
                connection.execute(
                    """
                    INSERT INTO note_trust(note_id, usefulness_score, successful_top1_count, successful_top5_count, selected_count, useful_count, not_useful_count, failure_count, last_used_at, updated_at)
                    VALUES ('concept/jwt-rotation-current', 0.7, 2, 2, 2, 2, 0, 0, datetime('now'), datetime('now'))
                    """
                )
                connection.execute(
                    """
                    INSERT INTO note_trust(note_id, usefulness_score, successful_top1_count, successful_top5_count, selected_count, useful_count, not_useful_count, failure_count, last_used_at, updated_at)
                    VALUES ('concept/jwt-rotation-legacy', 0.4, 1, 1, 2, 1, 1, 1, datetime('now'), datetime('now'))
                    """
                )
            connection.close()

            current_result = retrieve(config, "what is the current jwt rotation strategy", top_k=2)
            self.assertEqual(current_result.hits[0].id, "concept/jwt-rotation-current")

            historical_result = retrieve(config, "what was the previous legacy jwt rotation strategy", top_k=2)
            self.assertEqual(historical_result.hits[0].id, "concept/jwt-rotation-legacy")


if __name__ == "__main__":
    unittest.main()

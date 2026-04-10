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
from second_brain.retrieval import retrieve
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class Phase4Test(unittest.TestCase):
    def test_weekly_hygiene_creates_contradictions_relearn_tasks_and_overview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
            _write(workspace / "brain/system/USER.md", "# USER\n")
            _write(
                workspace / "brain/system/ACTIVE.md",
                """
                # ACTIVE
                updated_at: 2026-04-10

                ## Current Focus
                - Tighten trust and freshness.

                ## Open Loops
                - [ ] Review contradiction backlog.
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
                workspace / "brain/wiki/projects/auth-platform.md",
                """
                ---
                id: project/auth-platform
                type: project
                title: Auth Platform
                aliases: [auth]
                status: active
                tags: [auth]
                updated_at: 2026-04-10
                created_at: 2026-01-01
                source_refs: []
                confidence: high
                ---

                Authentication platform.
                """,
            )
            _write(
                workspace / "brain/wiki/decisions/auth-database-v1.md",
                """
                ---
                id: decision/auth-database-v1
                type: decision
                title: Use Postgres for auth database
                aliases: [auth db]
                status: active
                tags: [auth, database]
                updated_at: 2020-01-10
                created_at: 2020-01-10
                last_verified_at: 2020-01-10
                verified_by: human
                entities: [project/auth-platform]
                source_refs: [session/2020-01-10-0900]
                confidence: high
                ---

                Postgres is the primary database.
                """,
            )
            _write(
                workspace / "brain/wiki/decisions/auth-database-v2.md",
                """
                ---
                id: decision/auth-database-v2
                type: decision
                title: Use CockroachDB for auth database
                aliases: [auth db new]
                status: active
                tags: [auth, database]
                updated_at: 2026-04-10
                created_at: 2026-04-10
                last_verified_at: 2026-04-10
                verified_by: human
                entities: [project/auth-platform]
                source_refs: [session/2026-04-10-0900]
                confidence: high
                ---

                CockroachDB is now the primary database.
                """,
            )
            _write(
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service
                aliases: [deploy auth]
                status: active
                tags: [deploy, auth]
                updated_at: 2024-01-10
                created_at: 2024-01-10
                last_verified_at: 2024-01-10
                verified_by: human
                linked_decisions: [decision/auth-database-v1]
                source_refs: [session/2024-01-10-0900]
                confidence: high
                ---

                Deploy the auth service.
                """,
            )
            _write(
                workspace / "brain/wiki/incidents/auth-db-cutover.md",
                """
                ---
                id: incident/auth-db-cutover
                type: incident
                title: Auth DB Cutover Follow-Up
                tags: [auth, incident]
                status: resolved
                updated_at: 2026-04-10
                created_at: 2026-04-10
                linked_procedures: [procedure/deploy-auth-service]
                linked_decisions: [decision/auth-database-v1]
                source_refs: [session/2026-04-10-1200]
                confidence: high
                ---

                Procedure needs updating after the database migration.
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

                [graph]
                enabled = true

                [trust]
                min_training_samples = 2
                section_limit = 3
                """,
            )

            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            sync_result = sync_vault(config, mode="full")
            self.assertEqual(sync_result.parse_errors, 0)

            connection = connect_db(config.paths.db_path)
            with connection:
                for query_id in range(1, 4):
                    cursor = connection.execute(
                        """
                        INSERT INTO retrieval_queries(query, query_type, classifier_confidence, retrieved, top_k, useful, top1_correct)
                        VALUES (?, 'decision', 1.0, ?, 2, 1, 1)
                        """,
                        ("auth database choice", json.dumps(["decision/auth-database-v2"])),
                    )
                    real_query_id = int(cursor.lastrowid)
                    connection.execute(
                        """
                        INSERT INTO retrieval_hits(query_id, note_id, rank, channel, raw_score, final_score, selected, useful)
                        VALUES (?, 'decision/auth-database-v2', 1, 'bm25', 0.8, 0.9, 1, 1)
                        """,
                        (real_query_id,),
                    )
                    connection.execute(
                        """
                        INSERT INTO retrieval_hit_features(query_id, note_id, exact_hit, bm25_rank, bm25_score, semantic_score, graph_score, freshness_score, type_prior_score, trust_score)
                        VALUES (?, 'decision/auth-database-v2', 0, 1, 0.8, 0.2, 0.1, 0.9, 0.9, 0.6)
                        """,
                        (real_query_id,),
                    )
            connection.close()

            result = run_job(config, job_name="weekly_hygiene", dry_run=False)
            self.assertGreaterEqual(result.stats["contradiction_reviews"], 1)
            self.assertGreaterEqual(result.stats["open_relearn_tasks"], 1)
            self.assertEqual(result.stats["rerank_fallback"], 0)

            active_text = (workspace / "brain/system/ACTIVE.md").read_text()
            self.assertIn("## Contradictions", active_text)
            self.assertIn("## Relearn Queue", active_text)

            overview_text = (workspace / "brain/wiki/overviews/maintenance.md").read_text()
            self.assertIn("Maintenance Overview", overview_text)
            self.assertIn("Contradiction items", overview_text)

            review_files = list((workspace / "brain/wiki/review").glob("*.md"))
            self.assertTrue(any("contradiction" in path.name for path in review_files))

        # tempdir cleanup covers artifacts

    def test_retrieval_penalizes_stale_current_truth_but_keeps_history_accessible(self) -> None:
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

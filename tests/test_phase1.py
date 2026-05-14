from __future__ import annotations

from pathlib import Path
import shutil
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
from second_brain.parser import parse_note
from second_brain.retrieval import retrieve
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class Phase1Test(unittest.TestCase):
    def test_sync_and_retrieve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)

            _write(
                workspace / "brain/system/MEMORY.md",
                """
                # MEMORY
                - Tests live in tests/
                """,
            )
            _write(
                workspace / "brain/system/USER.md",
                """
                # USER
                - Prefers explicit APIs
                """,
            )
            _write(
                workspace / "brain/system/ACTIVE.md",
                """
                # ACTIVE
                - Build the auth service memory
                """,
            )
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
                source_refs: [session/2026-02-12-1530]
                confidence: high
                valid_from: 2026-02-12
                last_verified_at: 2026-04-10
                verified_by: human
                owners: [person/sarah-chen]
                ---

                ## Decision

                Postgres is the primary store for auth user records and audit logs.
                """,
            )
            _write(
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service to Production
                aliases: [auth deploy]
                status: active
                tags: [deploy, auth, ops]
                updated_at: 2026-04-10
                created_at: 2026-04-09
                source_refs: [session/2026-04-09-1400]
                confidence: high
                evidence_refs: [session/2026-04-09-1400]
                verification: []
                reviewed_by: human
                reviewed_at: 2026-04-10
                last_verified_at: 2026-04-10
                verified_by: human
                applicability: auth-service repo
                ---

                ## Steps

                1. Merge the PR to main.
                2. Run make test-integration.
                3. Approve rollout in Argo CD.
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
            min_relevance_score = 0.0
                top_k = 5
                include_archive_fallback = true

                [ingest]
                chunk_tokens = 400
                chunk_overlap_tokens = 50
                default_archive_type = "session"
                """,
            )

            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            sync_result = sync_vault(config, mode="full")
            self.assertEqual(sync_result.parse_errors, 0)
            self.assertEqual(sync_result.synced_notes, 2)

            decision_result = retrieve(config, "Why did we pick Postgres for auth?", top_k=3)
            self.assertTrue(decision_result.hits)
            self.assertEqual(decision_result.hits[0].id, "decision/auth-postgres")

            procedure_result = retrieve(config, "auth deploy", top_k=3)
            self.assertTrue(procedure_result.hits)
            self.assertEqual(procedure_result.hits[0].id, "procedure/deploy-auth-service")

    def test_parser_and_sync_accept_optional_episode_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)

            _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
            _write(workspace / "brain/system/USER.md", "# USER\n")
            _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
            _write(workspace / "brain/system/AGENTS.md", "bridges: {}\n")
            note_path = workspace / "brain/wiki/incidents/auth-rollout-failure.md"
            _write(
                note_path,
                """
                ---
                id: incident/auth-rollout-failure
                type: incident
                title: Auth rollout failed on stale schema
                updated_at: 2026-04-14
                created_at: 2026-04-14
                source_refs: [session/2026-04-14-0900]
                confidence: high
                chain_id: trace/auth-rollout-recovery-2026-04-14
                chain_confidence: high
                chain_question: How do we recover the auth rollout after schema drift?
                chain_summary: Resolved rollout by reconciling schema state before deploy.
                problem_class: auth-rollout-recovery
                failure_summary: First deploy failed because the schema version lagged behind.
                attempt_summaries:
                  - Retried deploy without reconciling schema state.
                  - Verified migration history and then applied missing migration.
                solution_rationale: Reconcile schema state before re-running the deploy.
                ---

                The initial rollout failed until the database schema was reconciled.
                """,
            )

            parsed = parse_note(note_path)
            self.assertEqual(parsed.chain_id, "trace/auth-rollout-recovery-2026-04-14")
            self.assertEqual(parsed.chain_confidence, "high")
            self.assertEqual(parsed.problem_class, "auth-rollout-recovery")
            self.assertEqual(len(parsed.attempt_summaries), 2)

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
                enabled = false
                """,
            )

            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            result = sync_vault(config, mode="full")
            self.assertEqual(result.parse_errors, 0)
            connection = connect_db(config.paths.db_path)
            row = connection.execute(
                """
                SELECT chain_id, chain_confidence, problem_class, failure_summary,
                       attempt_summaries, solution_rationale
                FROM notes
                WHERE id = 'incident/auth-rollout-failure'
                """
            ).fetchone()
            connection.close()

            self.assertEqual(str(row["chain_id"]), "trace/auth-rollout-recovery-2026-04-14")
            self.assertEqual(str(row["chain_confidence"]), "high")
            self.assertEqual(str(row["problem_class"]), "auth-rollout-recovery")
            self.assertIn("schema version lagged behind", str(row["failure_summary"]))
            self.assertIn("migration history", str(row["attempt_summaries"]))
            self.assertIn("Reconcile schema state", str(row["solution_rationale"]))

    def test_invalid_episode_metadata_degrades_without_parse_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)

            _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
            _write(workspace / "brain/system/USER.md", "# USER\n")
            _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
            _write(workspace / "brain/system/AGENTS.md", "bridges: {}\n")
            note_path = workspace / "brain/wiki/concepts/retry-budget.md"
            _write(
                note_path,
                """
                ---
                id: concept/retry-budget
                type: concept
                title: Retry Budget
                updated_at: 2026-04-14
                created_at: 2026-04-14
                source_refs: [session/2026-04-14-1000]
                confidence: high
                chain_confidence: definitely
                attempt_summaries:
                  answer: this should degrade instead of failing
                solution_rationale:
                ---

                Retry budgets cap repeated attempts on flaky work.
                """,
            )

            parsed = parse_note(note_path)
            self.assertIsNone(parsed.chain_confidence)
            self.assertEqual(parsed.attempt_summaries, [])
            self.assertIsNone(parsed.solution_rationale)

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
                enabled = false
                """,
            )

            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            result = sync_vault(config, mode="full")
            self.assertEqual(result.parse_errors, 0)
            connection = connect_db(config.paths.db_path)
            row = connection.execute(
                """
                SELECT chain_confidence, attempt_summaries, solution_rationale
                FROM notes
                WHERE id = 'concept/retry-budget'
                """
            ).fetchone()
            connection.close()

            self.assertIsNone(row["chain_confidence"])
            self.assertEqual(str(row["attempt_summaries"]), "[]")
            self.assertIsNone(row["solution_rationale"])

    def test_later_migrations_apply_cleanly_on_existing_populated_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)

            _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
            _write(workspace / "brain/system/USER.md", "# USER\n")
            _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
            _write(workspace / "brain/system/AGENTS.md", "bridges: {}\n")
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
                enabled = false
                """,
            )

            partial_migrations = workspace / "partial_migrations"
            partial_migrations.mkdir(parents=True, exist_ok=True)
            for name in (
                "001_initial_schema.sql",
                "002_phase2_semantic_and_ingest.sql",
                "003_phase3_graph_cache.sql",
                "004_phase4_trust_freshness.sql",
                "005_phase6_runtime_and_lifecycle.sql",
            ):
                shutil.copy(ROOT / "migrations" / name, partial_migrations / name)

            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, partial_migrations)
            with connection:
                connection.execute(
                    """
                    INSERT INTO notes(
                        id, type, title, status, confidence, tags, entities, source_refs,
                        valid_from, valid_to, updated_at, created_at, last_verified_at,
                        verified_by, last_observed_at, overlay_class, lifecycle_state,
                        body_path, summary, content_hash, revision
                    )
                    VALUES (
                        'concept/pre-migration', 'concept', 'Pre Migration Concept', 'active', 'high',
                        '[]', '[]', '[]', NULL, NULL, '2026-04-14', '2026-04-14', NULL, NULL, NULL,
                        NULL, 'canonical', ?, 'Before later migrations.', 'hash', 0
                    )
                    """,
                    (str((workspace / "brain/wiki/concepts/pre-migration.md").resolve()),),
                )
            connection.close()

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            row = connection.execute(
                """
                SELECT chain_id, attempt_summaries, solution_rationale
                FROM notes
                WHERE id = 'concept/pre-migration'
                """
            ).fetchone()
            metrics_tables = {
                str(table["name"])
                for table in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('session_metrics', 'runtime_packet_metrics')"
                ).fetchall()
            }
            connection.close()

            self.assertIsNone(row["chain_id"])
            self.assertEqual(str(row["attempt_summaries"]), "[]")
            self.assertIsNone(row["solution_rationale"])
            self.assertEqual(metrics_tables, {"session_metrics", "runtime_packet_metrics"})


if __name__ == "__main__":
    unittest.main()

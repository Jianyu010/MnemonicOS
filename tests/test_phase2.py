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
from second_brain.ingest import ingest_session
from second_brain.jobs import run_job
from second_brain.retrieval import retrieve
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class Phase2Test(unittest.TestCase):
    def test_ingest_semantic_retrieval_and_review_jobs(self) -> None:
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

                ## Current Focus
                - Build MnemonicOS
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
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service to Production
                aliases: [auth deploy, shared-alias]
                status: active
                tags: [deploy, auth, ops]
                updated_at: 2026-04-10
                created_at: 2026-04-09
                source_refs: [session/2026-04-09-1400]
                confidence: high
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
            _write(
                workspace / "brain/wiki/projects/auth-platform.md",
                """
                ---
                id: project/auth-platform
                type: project
                title: Auth Platform
                aliases: [shared-alias]
                status: active
                tags: [auth]
                updated_at: 2026-04-10
                created_at: 2026-04-01
                source_refs: []
                confidence: high
                ---

                Authentication services and infrastructure.
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
                enabled = true
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
            self.assertEqual(sync_result.synced_notes, 2)

            semantic_result = retrieve(config, "How do we release the auth service to production?", top_k=3)
            self.assertTrue(semantic_result.hits)
            self.assertEqual(semantic_result.hits[0].id, "procedure/deploy-auth-service")
            self.assertIn("semantic", semantic_result.hits[0].channels)

            ingest_result = ingest_session(
                config,
                agent="codex",
                slug="pooling-review",
                content=textwrap.dedent(
                    """
                    # Session Notes

                    We investigated how to smooth out connection spikes.

                    ```memory
                    items:
                      - type: decision
                        title: Use PgBouncer for auth connection pooling
                        body: PgBouncer will sit in front of Postgres to absorb connection spikes.
                        tags: [auth, database]
                        aliases: [pooler choice]
                        confidence: high
                      - type: procedure
                        title: Rotate auth deploy checklist
                        steps:
                          - Run smoke tests
                          - Approve rollout
                        confidence: medium
                      - type: preference
                        title: Prefer async PR reviews for infra changes
                        confidence: high
                    ```
                    """
                ),
            )
            self.assertGreater(ingest_result.chunk_count, 0)
            self.assertIn("decision/use-pgbouncer-for-auth-connection-pooling", ingest_result.promoted_notes)
            self.assertTrue(ingest_result.review_items)
            self.assertTrue(Path(ingest_result.raw_path).exists())

            updated_user = (workspace / "brain/system/USER.md").read_text()
            self.assertIn("Prefer async PR reviews for infra changes", updated_user)

            decision_result = retrieve(config, "What pooler should we use for auth database traffic?", top_k=3)
            self.assertTrue(decision_result.hits)
            self.assertEqual(decision_result.hits[0].id, "decision/use-pgbouncer-for-auth-connection-pooling")

            dry_run = run_job(config, job_name="daily_consolidation", dry_run=True)
            self.assertGreaterEqual(dry_run.stats["duplicate_aliases"], 1)

            live_run = run_job(config, job_name="daily_consolidation", dry_run=False)
            self.assertTrue(live_run.review_items_created)
            self.assertTrue(any("duplicate-alias-shared-alias" in item for item in live_run.review_items_created))


if __name__ == "__main__":
    unittest.main()

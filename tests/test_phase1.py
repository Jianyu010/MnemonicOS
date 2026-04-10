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


if __name__ == "__main__":
    unittest.main()

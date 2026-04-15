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
from second_brain.models import ResolveTaskContextRequest
from second_brain.runtime import resolve_task_context
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class RuntimeProtocolTest(unittest.TestCase):
    def _prepare_workspace(self, workspace: Path) -> tuple[Path, object]:
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
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config_path, config

    def test_resolve_task_context_returns_reuse_for_current_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _, config = self._prepare_workspace(workspace)
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
                updated_at: 2026-04-12
                created_at: 2026-04-12
                last_verified_at: 2026-04-12
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                Deploy the auth service with the current checklist.
                """,
            )
            sync_vault(config, mode="full")

            result = resolve_task_context(
                config,
                ResolveTaskContextRequest(
                    bridge_id="api-client",
                    session_id="session-1",
                    task_id="task-1",
                    task_text="how do we deploy auth service",
                    task_kind="coding",
                    temporality="current",
                    token_budget=1800,
                ),
            )

            self.assertEqual(result.retrieval_state, "sufficient_current")
            self.assertEqual(result.recommendation, "reuse")
            self.assertEqual(result.verification_action, "none")
            self.assertEqual(result.memory_packet.primary_memories[0].id, "procedure/deploy-auth-service")

    def test_resolve_task_context_marks_suspect_memory_for_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _, config = self._prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/concepts/jwt-rotation.md",
                """
                ---
                id: concept/jwt-rotation
                type: concept
                title: JWT Rotation Strategy
                aliases: [jwt rotation]
                status: active
                tags: [auth, jwt]
                updated_at: 2026-04-12
                created_at: 2026-04-12
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                Rotate JWT signing keys every 30 days.
                """,
            )
            sync_vault(config, mode="full")
            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute(
                    """
                    INSERT INTO note_freshness(note_id, staleness_score, freshness_state)
                    VALUES ('concept/jwt-rotation', 0.55, 'suspect')
                    """,
                )
            connection.close()

            result = resolve_task_context(
                config,
                ResolveTaskContextRequest(
                    bridge_id="api-client",
                    session_id="session-2",
                    task_id="task-2",
                    task_text="what is the jwt rotation strategy",
                    task_kind="architecture",
                    temporality="current",
                    token_budget=1200,
                ),
            )

            self.assertEqual(result.retrieval_state, "partial")
            self.assertEqual(result.recommendation, "reuse_with_verification")
            self.assertEqual(result.verification_action, "verify_current_truth")
            self.assertIn("concept/jwt-rotation", result.memory_packet.verification_needed)

    def test_resolve_task_context_allows_historical_reuse_for_stale_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _, config = self._prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/incidents/legacy-auth-cutover.md",
                """
                ---
                id: incident/legacy-auth-cutover
                type: incident
                title: Legacy Auth Cutover
                aliases: [legacy auth cutover]
                status: resolved
                tags: [auth, migration]
                updated_at: 2024-01-01
                created_at: 2024-01-01
                source_refs: [session/2024-01-01-0900]
                confidence: high
                ---

                The previous legacy auth cutover required manual rollback checks.
                """,
            )
            sync_vault(config, mode="full")
            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute(
                    """
                    INSERT INTO note_freshness(note_id, staleness_score, freshness_state)
                    VALUES ('incident/legacy-auth-cutover', 0.9, 'stale')
                    """,
                )
            connection.close()

            result = resolve_task_context(
                config,
                ResolveTaskContextRequest(
                    bridge_id="api-client",
                    session_id="session-3",
                    task_id="task-3",
                    task_text="what was the previous legacy auth cutover process",
                    task_kind="research",
                    temporality="historical",
                    token_budget=900,
                ),
            )

            self.assertEqual(result.retrieval_state, "sufficient_historical")
            self.assertEqual(result.recommendation, "reuse")
            self.assertEqual(result.verification_action, "treat_as_historical_only")

    def test_resolve_task_context_warns_on_contested_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _, config = self._prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/concepts/current-auth-policy.md",
                """
                ---
                id: concept/current-auth-policy
                type: concept
                title: Current Auth Policy
                status: active
                lifecycle_state: contested
                updated_at: 2026-04-13
                created_at: 2026-04-13
                source_refs: [session/2026-04-13-0900]
                confidence: high
                ---

                Current auth policy is under dispute pending review.
                """,
            )
            sync_vault(config, mode="full")

            result = resolve_task_context(
                config,
                ResolveTaskContextRequest(
                    bridge_id="api-client",
                    session_id="session-lifecycle",
                    task_id="task-lifecycle",
                    task_text="what is the current auth policy",
                    task_kind="architecture",
                    temporality="current",
                    token_budget=1200,
                ),
            )

            self.assertIn("concept/current-auth-policy", result.memory_packet.verification_needed)
            self.assertTrue(any("lifecycle state 'contested'" in warning for warning in result.memory_packet.warnings))

    def test_resolve_task_context_builds_bounded_primary_packet_by_memory_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _, config = self._prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service
                aliases: [deploy auth]
                status: active
                tags: [auth]
                updated_at: 2026-04-12
                created_at: 2026-04-12
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                Deploy the auth service.
                """,
            )
            _write(
                workspace / "brain/wiki/decisions/auth-database.md",
                """
                ---
                id: decision/auth-database
                type: decision
                title: Primary Datastore Choice
                aliases: [primary datastore]
                status: active
                tags: [database]
                updated_at: 2026-04-12
                created_at: 2026-04-12
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                We use Postgres as the primary datastore.
                """,
            )
            _write(
                workspace / "brain/wiki/incidents/auth-deploy-failure.md",
                """
                ---
                id: incident/auth-deploy-failure
                type: incident
                title: Auth Deploy Failure
                aliases: [auth deploy failure]
                status: resolved
                tags: [auth]
                updated_at: 2026-04-12
                created_at: 2026-04-12
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                The deploy failed once due to an outdated checklist.
                """,
            )
            _write(
                workspace / "brain/wiki/projects/auth-platform.md",
                """
                ---
                id: project/auth-platform
                type: project
                title: Auth Platform
                aliases: [auth platform]
                status: active
                tags: [auth]
                updated_at: 2026-04-12
                created_at: 2026-04-12
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                The auth platform owns deployment and token rotation.
                """,
            )
            sync_vault(config, mode="full")

            result = resolve_task_context(
                config,
                ResolveTaskContextRequest(
                    bridge_id="api-client",
                    session_id="session-4",
                    task_id="task-4",
                    task_text="auth deploy",
                    task_kind="debugging",
                    token_budget=800,
                ),
            )

            self.assertEqual(len(result.memory_packet.primary_memories), 2)
            primary_types = {item.type for item in result.memory_packet.primary_memories}
            self.assertIn("procedure", primary_types)
            self.assertTrue(primary_types & {"decision", "incident", "project", "concept", "source"})

    def test_resolve_task_context_preserves_graph_relation_metadata_in_packet(self) -> None:
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
                enabled = true
                """,
            )
            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()
            _write(
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service
                aliases: [deploy auth]
                status: active
                tags: [auth]
                updated_at: 2026-04-12
                created_at: 2026-04-12
                linked_decisions: [decision/auth-database]
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                Deploy the auth service.
                """,
            )
            _write(
                workspace / "brain/wiki/concepts/auth-deploy-checklist.md",
                """
                ---
                id: concept/auth-deploy-checklist
                type: concept
                title: Auth Deploy Checklist Concept
                aliases: [deploy checklist]
                status: active
                tags: [auth]
                updated_at: 2026-04-12
                created_at: 2026-04-12
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                The deploy auth service checklist includes smoke validation and migration gating.
                """,
            )
            _write(
                workspace / "brain/wiki/decisions/auth-database.md",
                """
                ---
                id: decision/auth-database
                type: decision
                title: Primary Datastore Choice
                aliases: [primary datastore]
                status: active
                tags: [database]
                updated_at: 2026-04-12
                created_at: 2026-04-12
                source_refs: [session/2026-04-12-0900]
                confidence: high
                ---

                We use Postgres as the primary datastore.
                """,
            )
            sync_vault(config, mode="full")

            result = resolve_task_context(
                config,
                ResolveTaskContextRequest(
                    bridge_id="api-client",
                    session_id="session-5",
                    task_id="task-5",
                    task_text="how do we deploy auth service",
                    task_kind="coding",
                    token_budget=1800,
                ),
            )

            primary_ids = {item.id for item in result.memory_packet.primary_memories}
            self.assertIn("decision/auth-database", primary_ids)
            related = next(item for item in result.memory_packet.primary_memories if item.id == "decision/auth-database")
            self.assertIn("linked_decision", related.relation_types)
            self.assertIn("relations:linked_decision", related.provenance)

    def test_resolve_task_context_adds_bounded_same_episode_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _, config = self._prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/procedures/active/recover-auth-rollout.md",
                """
                ---
                id: procedure/recover-auth-rollout
                type: procedure
                title: Recover Auth Rollout
                status: active
                chain_id: trace/auth-rollout-recovery
                updated_at: 2026-04-14
                created_at: 2026-04-14
                source_refs: [session/2026-04-14-0900]
                confidence: high
                ---

                Reconcile schema state before rerunning deploy.
                """,
            )
            _write(
                workspace / "brain/wiki/incidents/auth-rollout-failure.md",
                """
                ---
                id: incident/auth-rollout-failure
                type: incident
                title: Auth Rollout Failure
                status: resolved
                chain_id: trace/auth-rollout-recovery
                updated_at: 2026-04-14
                created_at: 2026-04-14
                source_refs: [session/2026-04-14-0900]
                confidence: high
                ---

                The rollout failed until schema state was reconciled.
                """,
            )
            _write(
                workspace / "brain/wiki/decisions/auth-rollout-decision.md",
                """
                ---
                id: decision/auth-rollout-decision
                type: decision
                title: Auth Rollout Recovery Decision
                status: active
                chain_id: trace/auth-rollout-recovery
                updated_at: 2026-04-14
                created_at: 2026-04-14
                source_refs: [session/2026-04-14-0900]
                confidence: high
                ---

                Use schema reconciliation before retrying rollout.
                """,
            )
            _write(
                workspace / "brain/wiki/concepts/schema-reconciliation.md",
                """
                ---
                id: concept/schema-reconciliation
                type: concept
                title: Schema Reconciliation
                status: active
                chain_id: trace/auth-rollout-recovery
                updated_at: 2026-04-14
                created_at: 2026-04-14
                source_refs: [session/2026-04-14-0900]
                confidence: high
                ---

                Reconcile schema state before rerunning deploy.
                """,
            )
            sync_vault(config, mode="full")

            result = resolve_task_context(
                config,
                ResolveTaskContextRequest(
                    bridge_id="api-client",
                    session_id="session-chain-context",
                    task_id="task-chain-context",
                    task_text="how do we recover auth rollout",
                    task_kind="debugging",
                    token_budget=800,
                ),
            )

            self.assertEqual(result.memory_packet.primary_memories[0].id, "procedure/recover-auth-rollout")
            self.assertEqual(len(result.memory_packet.same_episode_context), 1)
            self.assertIn(
                result.memory_packet.same_episode_context[0].id,
                {"incident/auth-rollout-failure", "decision/auth-rollout-decision", "concept/schema-reconciliation"},
            )
            self.assertEqual(result.memory_packet.same_episode_context[0].chain_id, "trace/auth-rollout-recovery")
            self.assertEqual(len(result.memory_packet.primary_memories), 2)

    def test_resolve_task_context_keeps_flat_factual_queries_without_episode_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _, config = self._prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/concepts/redis-expiry.md",
                """
                ---
                id: concept/redis-expiry
                type: concept
                title: Redis Expiry
                status: active
                updated_at: 2026-04-14
                created_at: 2026-04-14
                source_refs: [session/2026-04-14-1000]
                confidence: high
                ---

                Redis expires keys lazily and actively, not eagerly.
                """,
            )
            sync_vault(config, mode="full")

            result = resolve_task_context(
                config,
                ResolveTaskContextRequest(
                    bridge_id="api-client",
                    session_id="session-flat",
                    task_id="task-flat",
                    task_text="how does redis expire keys",
                    task_kind="knowledge",
                    token_budget=1200,
                ),
            )

            self.assertEqual(result.memory_packet.primary_memories[0].id, "concept/redis-expiry")
            self.assertEqual(result.memory_packet.same_episode_context, [])
            connection = connect_db(config.paths.db_path)
            row = connection.execute(
                """
                SELECT task_kind, primary_memory_count, same_episode_context_count
                FROM runtime_packet_metrics
                WHERE query_id = ?
                """,
                (result.query_id,),
            ).fetchone()
            connection.close()
            self.assertEqual(str(row["task_kind"]), "knowledge")
            self.assertEqual(int(row["primary_memory_count"]), len(result.memory_packet.primary_memories))
            self.assertEqual(int(row["same_episode_context_count"]), 0)


if __name__ == "__main__":
    unittest.main()

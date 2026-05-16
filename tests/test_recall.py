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
from second_brain.embedding_providers import embedding_model_key
from second_brain.ingest import ingest_session
from second_brain.query_parser import QueryHints, parse_query
from second_brain.recall import (
    decide_archive_policy,
    merge_wiki_candidates,
    recall,
    run_archive_channel,
    run_bm25_channel,
    run_exact_channel,
    run_graph_channel,
    run_semantic_channel,
)
from second_brain.sync import sync_vault
from second_brain.semantics import encode_text


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class RecallStageTest(unittest.TestCase):
    def _prepare_workspace(self, workspace: Path, *, embeddings_enabled: bool = False, graph_enabled: bool = True) -> object:
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

            [retrieval]
            min_relevance_score = 0.0
            top_k = 5
            include_archive_fallback = true

            [embeddings]
            enabled = {str(embeddings_enabled).lower()}
            provider = "hash"
            model = "hash-256-v1"
            dimensions = 256

            [graph]
            enabled = {str(graph_enabled).lower()}
            focal_limit = 3
            expand_limit = 8
            """,
        )
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def _parse(self, config: object, query: str, *, bridge_id: str = "codex"):
        connection = connect_db(config.paths.db_path)
        try:
            return parse_query(query, QueryHints(), config=config, connection=connection, bridge_id=bridge_id)
        finally:
            connection.close()

    def _seed_semantic_vector(self, config: object, note_id: str, text: str) -> None:
        vector = encode_text(text, dimensions=config.embeddings.dimensions)
        connection = connect_db(config.paths.db_path)
        with connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO note_vectors(note_id, model, dimensions, vector_json, source_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    note_id,
                    embedding_model_key(config),
                    config.embeddings.dimensions,
                    json.dumps(vector),
                    f"source:{note_id}",
                ),
            )
        connection.close()

    def test_exact_channel_uses_full_query_and_anchor_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, graph_enabled=False)
            _write(
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service
                aliases: [deploy-auth, auth deploy]
                status: active
                updated_at: 2026-05-12
                created_at: 2026-05-12
                source_refs: [session/2026-05-12-0900]
                confidence: high
                ---

                Deploy the auth service with the release checklist.
                """,
            )
            sync_vault(config, mode="full")
            parsed = self._parse(config, "deploy-auth")
            connection = connect_db(config.paths.db_path)
            try:
                candidates = run_exact_channel(connection, parsed)
            finally:
                connection.close()

            self.assertEqual([candidate.id for candidate in candidates], ["procedure/deploy-auth-service"])
            self.assertEqual(candidates[0].exact_rank, 1)
            self.assertIn("exact", candidates[0].channels)

    def test_bm25_channel_uses_expanded_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, graph_enabled=False)
            _write(
                workspace / "brain/wiki/procedures/active/publish-auth-service.md",
                """
                ---
                id: procedure/publish-auth-service
                type: procedure
                title: Publish Auth Service
                status: active
                updated_at: 2026-05-12
                created_at: 2026-05-12
                source_refs: [session/2026-05-12-0901]
                confidence: high
                ---

                Publish the auth service after validation succeeds.
                """,
            )
            sync_vault(config, mode="full")
            parsed = self._parse(config, "deploy auth service")
            self.assertIn("publish", parsed.expanded_terms)
            connection = connect_db(config.paths.db_path)
            try:
                candidates = run_bm25_channel(connection, parsed)
            finally:
                connection.close()

            self.assertEqual(candidates[0].id, "procedure/publish-auth-service")
            self.assertEqual(candidates[0].bm25_rank, 1)
            self.assertIsNotNone(candidates[0].bm25_score)

    def test_bm25_channel_supports_chinese_note_queries_without_body_pollution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, graph_enabled=False)
            _write(
                workspace / "brain/wiki/concepts/chinese-retrieval.md",
                """
                ---
                id: concept/chinese-retrieval
                type: concept
                title: 中文检索支持
                status: active
                updated_at: 2026-05-16
                created_at: 2026-05-16
                source_refs: [session/2026-05-16-cn]
                confidence: high
                ---

                系统支持中文检索和嵌入。
                """,
            )
            sync_vault(config, mode="full")
            parsed = self._parse(config, "系统支持中文吗")
            connection = connect_db(config.paths.db_path)
            try:
                candidates = run_bm25_channel(connection, parsed)
                stored = connection.execute(
                    "SELECT body, search_body FROM notes_search WHERE note_id = ?",
                    ("concept/chinese-retrieval",),
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(candidates[0].id, "concept/chinese-retrieval")
            self.assertEqual(str(stored["body"]).strip(), "系统支持中文检索和嵌入。")
            self.assertIn("中文", str(stored["search_body"]))

    def test_semantic_channel_returns_scored_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, embeddings_enabled=True, graph_enabled=False)
            _write(
                workspace / "brain/wiki/concepts/jwt-rotation.md",
                """
                ---
                id: concept/jwt-rotation
                type: concept
                title: JWT Rotation Strategy
                status: active
                updated_at: 2026-05-12
                created_at: 2026-05-12
                source_refs: [session/2026-05-12-0902]
                confidence: high
                ---

                Rotate JWT signing keys every 30 days and verify issuer overlap.
                """,
            )
            sync_vault(config, mode="full")
            self._seed_semantic_vector(
                config,
                "concept/jwt-rotation",
                "jwt rotation strategy rotate signing keys every 30 days",
            )
            parsed = self._parse(config, "jwt rotation strategy")
            connection = connect_db(config.paths.db_path)
            try:
                candidates = run_semantic_channel(connection, config, parsed)
            finally:
                connection.close()

            self.assertEqual(candidates[0].id, "concept/jwt-rotation")
            self.assertGreater(candidates[0].semantic_score or 0.0, 0.0)
            self.assertIn("semantic", candidates[0].channels)

    def test_graph_channel_expands_from_direct_seed_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, graph_enabled=True)
            _write(
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service
                status: active
                updated_at: 2026-05-12
                created_at: 2026-05-12
                source_refs: [session/2026-05-12-0903]
                confidence: high
                relations:
                  - target: concept/canary-slice-pattern
                    type: linked_note
                ---

                Deploy the auth service with the rollout checklist.
                """,
            )
            _write(
                workspace / "brain/wiki/concepts/canary-slice-pattern.md",
                """
                ---
                id: concept/canary-slice-pattern
                type: concept
                title: Canary Slice Pattern
                status: active
                updated_at: 2026-05-12
                created_at: 2026-05-12
                source_refs: [session/2026-05-12-0904]
                confidence: high
                ---

                Start with one pod, verify health, then widen traffic gradually.
                """,
            )
            sync_vault(config, mode="full")
            parsed = self._parse(config, "deploy auth service")
            connection = connect_db(config.paths.db_path)
            try:
                direct = merge_wiki_candidates(
                    exact_candidates=run_exact_channel(connection, parsed),
                    bm25_candidates=run_bm25_channel(connection, parsed),
                    semantic_candidates=[],
                    graph_candidates=[],
                )
                graph_candidates, seed_ids = run_graph_channel(connection, config, direct_candidates=direct)
            finally:
                connection.close()

            self.assertEqual(seed_ids[0], "procedure/deploy-auth-service")
            self.assertIn("procedure/deploy-auth-service", seed_ids)
            self.assertEqual(graph_candidates[0].id, "concept/canary-slice-pattern")
            self.assertEqual(graph_candidates[0].graph_depth, 1)
            self.assertIn("linked_note", graph_candidates[0].relation_types)

    def test_archive_policy_and_archive_channel_respect_raw_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, graph_enabled=False)
            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute(
                    """
                    INSERT INTO archive(id, type, path, ingested_at, agent, tags, chunk_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "session/test-raw",
                        "session",
                        str(workspace / "brain/raw/sessions/test-raw.md"),
                        "2026-05-12T09:00:00Z",
                        "codex",
                        "[]",
                        1,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO archive_chunks(archive_id, chunk_index, body, summary, token_count, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "session/test-raw",
                        0,
                        "The raw session transcript says deploy auth with canary first.",
                        "Raw transcript for auth deploy",
                        10,
                        "hash:chunk-0",
                    ),
                )
            parsed = self._parse(config, "show the original auth deploy transcript")
            policy = decide_archive_policy(parsed, include_archive_fallback=True)
            candidates = run_archive_channel(connection, parsed, archive_policy=policy)
            connection.close()

            self.assertTrue(policy.allowed)
            self.assertEqual(policy.mode, "explicit_raw_intent")
            self.assertEqual(candidates[0].archive_id, "session/test-raw")
            self.assertIn("archive", candidates[0].channels)

    def test_archive_channel_supports_chinese_ingested_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, graph_enabled=False)
            result = ingest_session(
                config,
                bridge_id="codex",
                session_id="cn-archive",
                content=("系统支持中文检索和嵌入。" * 60),
            )
            self.assertGreater(result.chunk_count, 1)
            parsed = self._parse(config, "检索")
            policy = decide_archive_policy(parsed, include_archive_fallback=True)
            connection = connect_db(config.paths.db_path)
            try:
                candidates = run_archive_channel(connection, parsed, archive_policy=policy)
                row = connection.execute(
                    "SELECT body, search_body FROM archive_chunks WHERE archive_id = ? ORDER BY chunk_index LIMIT 1",
                    (result.archive_id,),
                ).fetchone()
            finally:
                connection.close()

            self.assertTrue(candidates)
            self.assertEqual(candidates[0].archive_id, result.archive_id)
            self.assertNotIn("系 统", str(row["body"]))
            self.assertIn("检索", str(row["search_body"]))

    def test_merge_preserves_multi_channel_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, embeddings_enabled=True, graph_enabled=False)
            _write(
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service
                aliases: [deploy-auth]
                status: active
                updated_at: 2026-05-12
                created_at: 2026-05-12
                source_refs: [session/2026-05-12-0905]
                confidence: high
                ---

                Deploy the auth service with rollout checks.
                """,
            )
            sync_vault(config, mode="full")
            self._seed_semantic_vector(config, "procedure/deploy-auth-service", "deploy auth service rollout checks")
            parsed = self._parse(config, "deploy-auth")
            connection = connect_db(config.paths.db_path)
            try:
                merged = merge_wiki_candidates(
                    exact_candidates=run_exact_channel(connection, parsed),
                    bm25_candidates=run_bm25_channel(connection, parsed),
                    semantic_candidates=run_semantic_channel(connection, config, parsed),
                    graph_candidates=[],
                )
            finally:
                connection.close()

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].id, "procedure/deploy-auth-service")
            self.assertTrue({"exact", "bm25", "semantic"}.issubset(merged[0].channels))

    def test_east_flange_to_recall_builds_meaningful_candidate_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, graph_enabled=True)
            _write(
                workspace / "brain/wiki/procedures/active/deploy-auth-service.md",
                """
                ---
                id: procedure/deploy-auth-service
                type: procedure
                title: Deploy Auth Service
                aliases: [deploy auth]
                status: active
                updated_at: 2026-05-12
                created_at: 2026-05-12
                source_refs: [session/2026-05-12-0906]
                confidence: high
                relations:
                  - target: concept/canary-slice-pattern
                    type: linked_note
                ---

                Deploy the auth service using the release checklist.
                """,
            )
            _write(
                workspace / "brain/wiki/concepts/canary-slice-pattern.md",
                """
                ---
                id: concept/canary-slice-pattern
                type: concept
                title: Canary Slice Pattern
                status: active
                updated_at: 2026-05-12
                created_at: 2026-05-12
                source_refs: [session/2026-05-12-0907]
                confidence: high
                ---

                Start with one pod, verify health, then widen traffic gradually.
                """,
            )
            sync_vault(config, mode="full")
            parsed = self._parse(config, "how do we deploy auth service", bridge_id="codex")
            connection = connect_db(config.paths.db_path)
            try:
                pool = recall(config, parsed, connection=connection)
            finally:
                connection.close()

            self.assertEqual(parsed.persona, "skill")
            self.assertGreaterEqual(pool.recall_metrics.bm25_count, 1)
            self.assertGreaterEqual(pool.recall_metrics.graph_count, 1)
            self.assertGreaterEqual(pool.recall_metrics.merged_wiki_count, 2)
            self.assertIn("procedure/deploy-auth-service", [candidate.id for candidate in pool.wiki_candidates])
            self.assertEqual(pool.archive_policy.mode, "fallback_only")


if __name__ == "__main__":
    unittest.main()

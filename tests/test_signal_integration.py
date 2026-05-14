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
from second_brain.models import CandidatePool, RecallCandidate
from second_brain.query_parser import QueryHints, parse_query
from second_brain.recall import recall
from second_brain.retrieval import retrieve
from second_brain.signal_integration import classify_summary_quality, integrate_signals
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class SignalIntegrationStageTest(unittest.TestCase):
    def _prepare_workspace(self, workspace: Path, *, graph_enabled: bool = False) -> object:
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
            enabled = false
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

    def _parse(self, config: object, query: str, *, query_type_hint: str | None = None):
        connection = connect_db(config.paths.db_path)
        try:
            return parse_query(
                query,
                QueryHints(query_type_hint=query_type_hint),
                config=config,
                connection=connection,
                bridge_id="codex",
            )
        finally:
            connection.close()

    def test_summary_quality_rejects_empty_title_only_candidates(self) -> None:
        quality, reasons, passed = classify_summary_quality(
            title="Deploy Auth Service",
            summary="Deploy Auth Service",
            excerpt="Deploy Auth Service",
        )

        self.assertEqual(quality, "title_only")
        self.assertFalse(passed)
        self.assertIn("title_only_summary", reasons)

    def test_integrate_signals_uses_defaults_for_missing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace)
            parsed = self._parse(config, "how to deploy auth service", query_type_hint="how_to")
            candidate = RecallCandidate(
                id="procedure/deploy-auth-service",
                title="Deploy Auth Service",
                type="procedure",
                status="active",
                chain_id=None,
                lifecycle_state="canonical",
                body_path=str(workspace / "brain/wiki/procedures/active/deploy-auth-service.md"),
                summary="Deploy auth with canary validation before widening traffic.",
                body="Deploy auth with canary validation before widening traffic.",
                channels={"bm25"},
                bm25_rank=1,
                bm25_score=0.5,
                updated_at="2026-05-12",
                created_at="2026-05-12",
            )
            pool = CandidatePool(parsed_query=parsed, wiki_candidates=[candidate])
            connection = connect_db(config.paths.db_path)
            try:
                enriched = integrate_signals(config, parsed, pool, connection=connection)
            finally:
                connection.close()

            signals = enriched.wiki_candidates[0].signals
            self.assertEqual(signals.trust_score, 0.0)
            self.assertEqual(signals.importance_score, 0.0)
            self.assertEqual(signals.freshness_state, "fresh")
            self.assertEqual(signals.type_prior_score, 0.9)
            self.assertTrue(signals.quality_pass)
            self.assertEqual(signals.summary_quality, "useful")
            self.assertEqual(signals.passive_feedback_state, "none")
            self.assertEqual(signals.passive_feedback_confidence, 0.0)

    def test_integrate_signals_loads_trust_freshness_importance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace)
            parsed = self._parse(config, "latest auth changes", query_type_hint="what_changed")
            note_id = "incident/auth-rollout-failure"
            candidate = RecallCandidate(
                id=note_id,
                title="Auth Rollout Failure",
                type="incident",
                status=None,
                chain_id=None,
                lifecycle_state="verified",
                body_path=str(workspace / "brain/wiki/incidents/auth-rollout-failure.md"),
                summary="Cause: stale rollout guidance caused auth deployment failure. Fix: verify canary health.",
                body="Cause: stale rollout guidance caused auth deployment failure. Fix: verify canary health.",
                channels={"bm25"},
                bm25_rank=1,
                bm25_score=0.7,
            )
            pool = CandidatePool(parsed_query=parsed, wiki_candidates=[candidate])
            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute(
                    """
                    INSERT INTO notes(id, type, title, body_path, summary, updated_at, created_at, content_hash)
                    VALUES (?, 'incident', 'Auth Rollout Failure', ?, ?, '2026-05-12', '2026-05-12', 'hash')
                    """,
                    (note_id, candidate.body_path, candidate.summary),
                )
                connection.execute(
                    """
                    INSERT INTO note_trust(note_id, usefulness_score, selected_count, useful_count, not_useful_count, failure_count)
                    VALUES (?, 0.8, 4, 3, 1, 0)
                    """,
                    (note_id,),
                )
                connection.execute(
                    """
                    INSERT INTO note_freshness(note_id, staleness_score, freshness_state)
                    VALUES (?, 0.4, 'suspect')
                    """,
                    (note_id,),
                )
                connection.execute(
                    "INSERT INTO note_importance(note_id, importance_score) VALUES (?, 0.65)",
                    (note_id,),
                )
                connection.execute(
                    """
                    INSERT INTO note_passive_feedback(note_id, feedback_state, confidence, method, evidence_type)
                    VALUES
                      (?, 'passive_used_likely', 0.72, 'extraction_inline', 'retrieval_attribution'),
                      (?, 'passive_useful_likely', 0.81, 'maintenance_post_hoc', 'broad_supporting_evidence'),
                      (?, 'passive_misleading_likely', 0.81, 'maintenance_post_hoc', 'contradictory_evidence')
                    """,
                    (note_id, note_id, note_id),
                )
            try:
                enriched = integrate_signals(config, parsed, pool, connection=connection)
            finally:
                connection.close()

            signals = enriched.wiki_candidates[0].signals
            self.assertAlmostEqual(signals.trust_score, 0.8)
            self.assertAlmostEqual(signals.freshness_score, 0.6)
            self.assertEqual(signals.freshness_state, "suspect")
            self.assertAlmostEqual(signals.importance_score, 0.65)
            self.assertAlmostEqual(signals.type_prior_score, 0.8)
            self.assertLess(signals.freshness_multiplier, 1.0)
            self.assertEqual(signals.passive_feedback_state, "passive_misleading_likely")
            self.assertAlmostEqual(signals.passive_feedback_confidence, 0.81)

    def test_integrate_signals_keeps_defaults_when_passive_feedback_table_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace)
            parsed = self._parse(config, "how to deploy auth service", query_type_hint="how_to")
            candidate = RecallCandidate(
                id="procedure/deploy-auth-service",
                title="Deploy Auth Service",
                type="procedure",
                status="active",
                chain_id=None,
                lifecycle_state="canonical",
                body_path=str(workspace / "brain/wiki/procedures/active/deploy-auth-service.md"),
                summary="Deploy auth with canary validation before widening traffic.",
                body="Deploy auth with canary validation before widening traffic.",
                channels={"bm25"},
                bm25_rank=1,
                bm25_score=0.5,
            )
            pool = CandidatePool(parsed_query=parsed, wiki_candidates=[candidate])
            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute("DROP TABLE IF EXISTS note_passive_feedback")
            try:
                enriched = integrate_signals(config, parsed, pool, connection=connection)
            finally:
                connection.close()

            signals = enriched.wiki_candidates[0].signals
            self.assertEqual(signals.passive_feedback_state, "none")
            self.assertEqual(signals.passive_feedback_confidence, 0.0)

    def test_east_flange_to_signal_integration_enriches_recall_pool(self) -> None:
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
                source_refs: [session/2026-05-12-1000]
                confidence: high
                summary: Deploy auth with canary validation before widening traffic.
                relations:
                  - target: concept/canary-slice-pattern
                    type: linked_note
                ---

                Deploy auth with canary validation before widening traffic.
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
                source_refs: [session/2026-05-12-1001]
                confidence: high
                summary: Start with one pod, verify health, then widen traffic gradually.
                ---

                Start with one pod, verify health, then widen traffic gradually.
                """,
            )
            sync_vault(config, mode="full")
            parsed = self._parse(config, "how do we deploy auth service", query_type_hint="how_to")
            connection = connect_db(config.paths.db_path)
            try:
                pool = recall(config, parsed, connection=connection)
                enriched = integrate_signals(config, parsed, pool, connection=connection)
            finally:
                connection.close()

            self.assertEqual(parsed.persona, "skill")
            ids = [item.candidate.id for item in enriched.wiki_candidates]
            self.assertIn("procedure/deploy-auth-service", ids)
            deploy = next(item for item in enriched.wiki_candidates if item.candidate.id == "procedure/deploy-auth-service")
            self.assertTrue(deploy.signals.quality_pass)
            self.assertEqual(deploy.signals.summary_quality, "useful")
            self.assertAlmostEqual(deploy.signals.type_prior_score, 0.9)

    def test_retrieve_logs_stage3_feature_values_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace)
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
                source_refs: [session/2026-05-12-1002]
                confidence: high
                summary: Deploy auth with canary validation before widening traffic.
                ---

                Deploy auth with canary validation before widening traffic.
                """,
            )
            sync_vault(config, mode="full")
            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute(
                    "INSERT INTO note_importance(note_id, importance_score) VALUES ('procedure/deploy-auth-service', 0.77)"
                )
            connection.close()

            result = retrieve(config, "how do we deploy auth service", query_type_hint="how_to", bridge_id="codex")
            self.assertEqual(result.hits[0].id, "procedure/deploy-auth-service")

            connection = connect_db(config.paths.db_path)
            try:
                row = connection.execute(
                    """
                    SELECT freshness_score, type_prior_score, trust_score, importance_score
                    FROM retrieval_hit_features
                    WHERE query_id = ? AND note_id = 'procedure/deploy-auth-service'
                    """,
                    (result.query_id,),
                ).fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(row)
            assert row is not None
            self.assertAlmostEqual(float(row["type_prior_score"]), 0.9)
            self.assertAlmostEqual(float(row["trust_score"]), 0.0)
            self.assertAlmostEqual(float(row["importance_score"]), 0.77)
            self.assertGreater(float(row["freshness_score"]), 0.0)


if __name__ == "__main__":
    unittest.main()

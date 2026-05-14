from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import ingest_session
from second_brain.jobs import run_job
from second_brain.models import (
    ArchiveCandidate,
    ArchiveSignals,
    CandidatePool,
    NoteSignals,
    RecallCandidate,
    SignalEnrichedArchiveCandidate,
    SignalEnrichedCandidate,
    SignalEnrichedCandidatePool,
)
from second_brain.reranker import rerank
from second_brain.retrieval import retrieve
from second_brain.trust import DEFAULT_RERANK_WEIGHTS, train_rerank_model


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _parsed(**overrides):
    data = {
        "raw": "how to deploy auth service",
        "tokens": ["how", "deploy", "auth", "service"],
        "query_type": "how_to",
        "classifier_confidence": 1.0,
        "temporality": "current",
        "top_k": 5,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _candidate(
    note_id: str,
    title: str,
    *,
    bm25_rank: int | None = 1,
    exact_rank: int | None = None,
    chain_id: str | None = None,
) -> RecallCandidate:
    return RecallCandidate(
        id=note_id,
        title=title,
        type="procedure",
        status="active",
        chain_id=chain_id,
        lifecycle_state="verified",
        body_path=f"/tmp/{note_id.replace('/', '-')}.md",
        summary=f"{title} has deploy guidance with canary checks.",
        body=f"{title} has deploy guidance with canary checks.",
        channels={"bm25"} if exact_rank is None else {"exact", "bm25"},
        exact_rank=exact_rank,
        bm25_rank=bm25_rank,
        bm25_score=0.5,
    )


class RerankerStageTest(unittest.TestCase):
    def _prepare_workspace(self, workspace: Path, *, auto_trigger_pipeline: bool = False):
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
            top_k = 5
            include_archive_fallback = true
            bm25_weight = 0.30
            semantic_weight = 0.0
            type_prior_weight = 0.0
            freshness_weight = 0.0
            memory_strength_weight = 0.0
            importance_weight = 0.60
            quality_weight = 0.40
            exact_alias_weight = 0.10

            [ingest]
            auto_trigger_pipeline = {str(auto_trigger_pipeline).lower()}

            [embeddings]
            enabled = false
            provider = "hash"
            model = "hash-256-v1"
            dimensions = 256

            [graph]
            enabled = false

            [trust]
            min_training_samples = 2
            """,
        )
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def _pool(self, *items: SignalEnrichedCandidate, parsed=None, archives=None) -> SignalEnrichedCandidatePool:
        return SignalEnrichedCandidatePool(
            parsed_query=parsed or _parsed(),
            wiki_candidates=list(items),
            archive_candidates=list(archives or []),
        )

    def test_exact_candidate_bypasses_heuristic_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            exact = SignalEnrichedCandidate(
                candidate=_candidate("procedure/exact", "Exact Deploy", exact_rank=2),
                signals=NoteSignals(quality_pass=False, freshness_score=0.0),
            )
            strong = SignalEnrichedCandidate(
                candidate=_candidate("procedure/strong", "Strong Deploy", bm25_rank=1),
                signals=NoteSignals(quality_pass=True, freshness_score=1.0, importance_score=1.0),
            )
            connection = connect_db(config.paths.db_path)
            try:
                ranked = rerank(config, self._pool(strong, exact), connection=connection, top_k=2)
            finally:
                connection.close()

            self.assertEqual(ranked.wiki_hits[0].hit.id, "procedure/exact")
            self.assertGreater(ranked.wiki_hits[0].hit.final_score, 999_000)

    def test_importance_and_quality_affect_order_without_db_signal_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            low_importance = SignalEnrichedCandidate(
                candidate=_candidate("procedure/low", "Alpha Low", bm25_rank=1),
                signals=NoteSignals(quality_pass=True, importance_score=0.0),
            )
            important = SignalEnrichedCandidate(
                candidate=_candidate("procedure/important", "Beta Important", bm25_rank=2),
                signals=NoteSignals(quality_pass=True, importance_score=1.0),
            )
            weak_quality = SignalEnrichedCandidate(
                candidate=_candidate("procedure/weak", "Gamma Weak", bm25_rank=1),
                signals=NoteSignals(quality_pass=False, importance_score=0.0),
            )
            connection = connect_db(config.paths.db_path)
            try:
                ranked = rerank(
                    config,
                    self._pool(low_importance, important, weak_quality),
                    connection=connection,
                    top_k=3,
                )
            finally:
                connection.close()

            self.assertEqual(ranked.wiki_hits[0].hit.id, "procedure/important")
            self.assertLess(
                next(item.hit.final_score for item in ranked.wiki_hits if item.hit.id == "procedure/weak"),
                next(item.hit.final_score for item in ranked.wiki_hits if item.hit.id == "procedure/low"),
            )

    def test_archive_lane_is_scored_separately_and_not_mixed_into_wiki_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            wiki = SignalEnrichedCandidate(
                candidate=_candidate("procedure/wiki", "Wiki Deploy", bm25_rank=1),
                signals=NoteSignals(quality_pass=True),
            )
            archive = SignalEnrichedArchiveCandidate(
                candidate=ArchiveCandidate(
                    id="session/archive#chunk-0",
                    archive_id="session/archive",
                    chunk_index=0,
                    archive_path="/tmp/archive.md",
                    summary="Archive deploy notes",
                    excerpt="Archive deploy notes",
                    channels={"archive"},
                    bm25_rank=1,
                    bm25_score=-0.5,
                ),
                signals=ArchiveSignals(quality_pass=True),
            )
            connection = connect_db(config.paths.db_path)
            try:
                ranked = rerank(config, self._pool(wiki, archives=[archive]), connection=connection, top_k=5)
            finally:
                connection.close()

            self.assertEqual([item.hit.id for item in ranked.wiki_hits], ["procedure/wiki"])
            self.assertEqual([item.hit.id for item in ranked.archive_hits], ["session/archive#chunk-0"])
            self.assertEqual([hit.id for hit in ranked.hits], ["procedure/wiki"])

    def test_equal_scores_sort_by_title_then_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            beta = SignalEnrichedCandidate(
                candidate=_candidate("procedure/beta", "Beta", bm25_rank=1),
                signals=NoteSignals(quality_pass=True),
            )
            alpha_b = SignalEnrichedCandidate(
                candidate=_candidate("procedure/alpha-b", "Alpha", bm25_rank=1),
                signals=NoteSignals(quality_pass=True),
            )
            alpha_a = SignalEnrichedCandidate(
                candidate=_candidate("procedure/alpha-a", "Alpha", bm25_rank=1),
                signals=NoteSignals(quality_pass=True),
            )
            connection = connect_db(config.paths.db_path)
            try:
                ranked = rerank(config, self._pool(beta, alpha_b, alpha_a), connection=connection, top_k=3)
            finally:
                connection.close()

            self.assertEqual(
                [item.hit.id for item in ranked.wiki_hits],
                ["procedure/alpha-a", "procedure/alpha-b", "procedure/beta"],
            )

    def test_same_chain_cap_leaves_room_for_distinct_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            same_a = SignalEnrichedCandidate(
                candidate=_candidate("procedure/same-a", "Alpha Same", bm25_rank=1, chain_id="chain/auth"),
                signals=NoteSignals(quality_pass=True),
            )
            same_b = SignalEnrichedCandidate(
                candidate=_candidate("procedure/same-b", "Beta Same", bm25_rank=2, chain_id="chain/auth"),
                signals=NoteSignals(quality_pass=True),
            )
            same_c = SignalEnrichedCandidate(
                candidate=_candidate("procedure/same-c", "Gamma Same", bm25_rank=3, chain_id="chain/auth"),
                signals=NoteSignals(quality_pass=True),
            )
            distinct = SignalEnrichedCandidate(
                candidate=_candidate("procedure/distinct", "Zulu Distinct", bm25_rank=4, chain_id="chain/ops"),
                signals=NoteSignals(quality_pass=True),
            )
            connection = connect_db(config.paths.db_path)
            try:
                ranked = rerank(
                    config,
                    self._pool(same_a, same_b, same_c, distinct),
                    connection=connection,
                    top_k=3,
                )
            finally:
                connection.close()

            self.assertEqual(
                [item.hit.id for item in ranked.wiki_hits],
                ["procedure/same-a", "procedure/same-b", "procedure/distinct"],
            )

    def test_train_rerank_model_uses_passive_labels_with_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute(
                    "INSERT INTO notes(id, type, title, body_path, summary, updated_at, created_at, content_hash) VALUES ('concept/good', 'concept', 'Good', '/tmp/good.md', 'good', '2026-05-13', '2026-05-13', 'a')"
                )
                connection.execute(
                    "INSERT INTO notes(id, type, title, body_path, summary, updated_at, created_at, content_hash) VALUES ('concept/bad', 'concept', 'Bad', '/tmp/bad.md', 'bad', '2026-05-13', '2026-05-13', 'b')"
                )
                for query_id, note_id, importance, quality in [
                    (1, "concept/good", 1.0, 1.0),
                    (2, "concept/bad", 0.0, 0.0),
                ]:
                    connection.execute(
                        "INSERT INTO retrieval_queries(id, query, retrieved, top_k) VALUES (?, 'q', '[]', 1)",
                        (query_id,),
                    )
                    connection.execute(
                        "INSERT INTO retrieval_hits(query_id, note_id, rank, channel, raw_score, final_score, selected) VALUES (?, ?, 1, 'bm25', 0.5, 0.5, 1)",
                        (query_id, note_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO retrieval_hit_features(
                            query_id, note_id, exact_hit, bm25_rank, bm25_score, semantic_score,
                            graph_score, freshness_score, type_prior_score, trust_score, importance_score,
                            quality_pass, quality_score, passive_feedback_state, passive_feedback_confidence
                        )
                        VALUES (?, ?, 0, 1, 0.5, 0.0, 0.0, 0.5, 0.0, 0.0, ?, ?, ?, 'none', 0.0)
                        """,
                        (query_id, note_id, importance, int(quality), quality),
                    )
                connection.execute(
                    """
                    INSERT INTO note_passive_feedback(note_id, feedback_state, confidence, method, evidence_type)
                    VALUES
                      ('concept/good', 'passive_useful_likely', 1.0, 'maintenance_post_hoc', 'supporting_evidence'),
                      ('concept/bad', 'passive_misleading_likely', 1.0, 'maintenance_post_hoc', 'contradiction')
                    """
                )

            try:
                model = train_rerank_model(connection, config)
            finally:
                connection.close()

            self.assertFalse(model.fallback)
            self.assertEqual(model.sample_count, 2)
            self.assertGreater(model.weights["importance_weight"], DEFAULT_RERANK_WEIGHTS["importance_weight"])
            self.assertGreater(model.weights["quality_weight"], DEFAULT_RERANK_WEIGHTS["quality_weight"])

            job_result = run_job(config, job_name="rerank_training")
            self.assertEqual(job_result.status, "ok")
            self.assertEqual(job_result.stats["sample_count"], 2)
            self.assertFalse(job_result.stats["fallback"])

    def test_ingested_session_archive_fallback_returns_stage4_archive_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace, auto_trigger_pipeline=False)
            ingest = ingest_session(
                config,
                bridge_id="codex",
                session_id="east-flange-stage4",
                content="""
                # Session

                The east flange calibration uses amber bolts and a two-pass torque sweep.
                Keep this raw session available for archive fallback retrieval.
                """,
            )
            self.assertGreater(ingest.chunk_count, 0)

            result = retrieve(
                config,
                "raw session east flange amber bolts two-pass torque",
                top_k=3,
                include_archive_fallback=True,
            )

            self.assertTrue(result.hits)
            self.assertEqual(result.hits[0].type, "archive_chunk")
            self.assertEqual(result.hits[0].channels, ["archive"])
            self.assertIn("amber bolts", result.hits[0].excerpt)

            connection = connect_db(config.paths.db_path)
            try:
                archive_row = connection.execute(
                    "SELECT rank, channel, final_score FROM retrieval_archive_hits WHERE query_id = ?",
                    (result.query_id,),
                ).fetchone()
                feature_rows = connection.execute(
                    "SELECT COUNT(*) AS count FROM retrieval_hit_features WHERE query_id = ?",
                    (result.query_id,),
                ).fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(archive_row)
            assert archive_row is not None
            self.assertEqual(str(archive_row["channel"]), "archive_bm25")
            self.assertEqual(int(feature_rows["count"]), 0)


if __name__ == "__main__":
    unittest.main()

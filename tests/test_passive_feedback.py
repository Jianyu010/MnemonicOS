from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.extraction.validation import validate_retrieval_attribution
from second_brain.maintenance import PassiveFeedbackResult, derive_passive_feedback


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _minimal_config(workspace: Path) -> Path:
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

        [maintenance]
        passive_feedback_enabled = true
        passive_feedback_inline_only = false
        """,
    )
    return config_path


def _system_files(workspace: Path) -> None:
    for name in ("MEMORY.md", "USER.md", "ACTIVE.md"):
        _write(workspace / "brain/system" / name, f"# {name}\n")
    _write(
        workspace / "brain/system/AGENTS.md",
        """
        bridges:
          local_cli:
            scope: [wiki]
            can_promote: false
            can_write_canonical: true
        """,
    )


def _setup_db(workspace: Path):
    for name in ("MEMORY.md", "USER.md", "ACTIVE.md"):
        _write(workspace / "brain/system" / name, f"# {name}\n")
    _write(workspace / "brain/system/AGENTS.md", "bridges: {}\n")
    config_path = _minimal_config(workspace)
    config = load_config(config_path, workspace)
    conn = connect_db(config.paths.db_path)
    run_migrations(conn, ROOT / "migrations")
    return config, conn


def _insert_note(conn: sqlite3.Connection, note_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO notes
            (id, type, title, summary, content_hash, body_path, updated_at, created_at)
        VALUES (?, 'concept', ?, 'summary', 'abc', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """,
        (note_id, note_id, f"wiki/concepts/active/{note_id.replace('/', '-')}.md"),
    )


def _insert_promotion_decision(
    conn: sqlite3.Connection,
    candidate_id: int,
    *,
    archive_id: str = "arc-001",
    decision: str = "new",
    target_note_id: str | None = None,
    retrieval_attribution_json: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO extraction_candidates
            (id, queue_id, candidate_index, candidate_type, title, payload_json,
             model_provider, model_name, promotion_state)
        VALUES (?, 1, ?, 'concept', 'Test', '{}', 'fixture', 'fixture-v1', 'promoted')
        """,
        (candidate_id, candidate_id),
    )
    conn.execute(
        """
        INSERT INTO promotion_decisions
            (candidate_id, decision, target_note_id, archive_id,
             check_errors_json, promotion_state, bridge_id, retrieval_attribution_json)
        VALUES (?, ?, ?, ?, '[]', 'promoted', 'job:test', ?)
        """,
        (candidate_id, decision, target_note_id, archive_id, retrieval_attribution_json),
    )


def _insert_extraction_queue(conn: sqlite3.Connection, queue_id: int, archive_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO extraction_queue
            (id, source_kind, archive_id, bridge_id, duplicate_state, status)
        VALUES (?, 'session', ?, 'job:test', 'unique', 'processed')
        """,
        (queue_id, archive_id),
    )


# ---------------------------------------------------------------------------
# validate_retrieval_attribution unit tests
# ---------------------------------------------------------------------------

class TestValidateRetrievalAttribution(unittest.TestCase):
    def test_none_returns_empty(self) -> None:
        assert validate_retrieval_attribution(None, warnings=[]) == []

    def test_non_list_warns(self) -> None:
        warnings: list[str] = []
        result = validate_retrieval_attribution({"note_id": "x"}, warnings=warnings)
        assert result == []
        assert any("not_list" in w for w in warnings)

    def test_valid_entry_passes(self) -> None:
        raw = [{"note_id": "concept/foo", "evidence_type": "paraphrase", "confidence": 0.8}]
        result = validate_retrieval_attribution(raw, warnings=[])
        assert len(result) == 1
        assert result[0]["note_id"] == "concept/foo"
        assert result[0]["confidence"] == 0.8

    def test_confidence_clamped(self) -> None:
        raw = [{"note_id": "concept/foo", "evidence_type": "paraphrase", "confidence": 1.5}]
        result = validate_retrieval_attribution(raw, warnings=[])
        assert result[0]["confidence"] == 1.0

    def test_missing_note_id_warns_and_drops(self) -> None:
        warnings: list[str] = []
        raw = [{"evidence_type": "paraphrase", "confidence": 0.5}]
        result = validate_retrieval_attribution(raw, warnings=warnings)
        assert result == []
        assert any("missing_note_id" in w for w in warnings)

    def test_unsupported_evidence_type_warns_and_drops(self) -> None:
        warnings: list[str] = []
        raw = [{"note_id": "concept/foo", "evidence_type": "INVALID", "confidence": 0.5}]
        result = validate_retrieval_attribution(raw, warnings=warnings)
        assert result == []
        assert any("unsupported_evidence_type" in w for w in warnings)

    def test_dedup_on_note_id_and_evidence_type(self) -> None:
        raw = [
            {"note_id": "concept/foo", "evidence_type": "paraphrase", "confidence": 0.8},
            {"note_id": "concept/foo", "evidence_type": "paraphrase", "confidence": 0.6},
        ]
        result = validate_retrieval_attribution(raw, warnings=[])
        assert len(result) == 1
        assert result[0]["confidence"] == 0.8  # first entry kept

    def test_different_evidence_types_both_kept(self) -> None:
        raw = [
            {"note_id": "concept/foo", "evidence_type": "paraphrase", "confidence": 0.8},
            {"note_id": "concept/foo", "evidence_type": "correction", "confidence": 0.6},
        ]
        result = validate_retrieval_attribution(raw, warnings=[])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Inline path tests
# ---------------------------------------------------------------------------

class TestDeriveInlinePath(unittest.TestCase):
    def test_inline_writes_passive_used_likely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-001")
                attribution = json.dumps([
                    {"note_id": "concept/foo", "evidence_type": "paraphrase", "confidence": 0.7}
                ])
                _insert_promotion_decision(
                    conn, 1,
                    archive_id="arc-001",
                    decision="new",
                    retrieval_attribution_json=attribution,
                )

            result = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            conn2 = connect_db(config.paths.db_path)
            rows = conn2.execute(
                "SELECT * FROM note_passive_feedback WHERE note_id = 'concept/foo'"
            ).fetchall()
            conn2.close()

            assert len(rows) == 1
            assert rows[0]["feedback_state"] == "passive_used_likely"
            assert rows[0]["method"] == "extraction_inline"
            assert result.inline_processed >= 1
            assert result.feedback_written >= 1

    def test_confirm_decision_boosts_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-002")
                _insert_note(conn, "concept/bar")
                attribution = json.dumps([
                    {"note_id": "concept/bar", "evidence_type": "semantic_reliance", "confidence": 0.6}
                ])
                _insert_promotion_decision(
                    conn, 1,
                    archive_id="arc-002",
                    decision="confirm",
                    target_note_id="concept/bar",
                    retrieval_attribution_json=attribution,
                )

            result = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            conn2 = connect_db(config.paths.db_path)
            row = conn2.execute(
                "SELECT confidence FROM note_passive_feedback WHERE note_id = 'concept/bar'"
            ).fetchone()
            conn2.close()

            assert row is not None
            # confirm/update boosts to 0.85
            assert row["confidence"] >= 0.85

    def test_inline_dedup_idempotent(self) -> None:
        """Second derive_passive_feedback call must not duplicate rows."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-003")
                attribution = json.dumps([
                    {"note_id": "concept/baz", "evidence_type": "paraphrase", "confidence": 0.7}
                ])
                _insert_promotion_decision(
                    conn, 1,
                    archive_id="arc-003",
                    retrieval_attribution_json=attribution,
                )

            derive_passive_feedback(conn, config, dry_run=False)
            result2 = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            conn2 = connect_db(config.paths.db_path)
            rows = conn2.execute(
                "SELECT * FROM note_passive_feedback WHERE note_id = 'concept/baz'"
            ).fetchall()
            conn2.close()

            assert len(rows) == 1  # UNIQUE constraint enforces idempotency
            assert result2.feedback_written == 0  # second run writes nothing

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-004")
                attribution = json.dumps([
                    {"note_id": "concept/dry", "evidence_type": "paraphrase", "confidence": 0.7}
                ])
                _insert_promotion_decision(
                    conn, 1,
                    archive_id="arc-004",
                    retrieval_attribution_json=attribution,
                )

            derive_passive_feedback(conn, config, dry_run=True)
            conn.close()

            conn2 = connect_db(config.paths.db_path)
            rows = conn2.execute("SELECT * FROM note_passive_feedback").fetchall()
            conn2.close()

            assert rows == []

    def test_no_attribution_skips_inline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-005")
                _insert_promotion_decision(conn, 1, archive_id="arc-005")

            result = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            assert result.feedback_written == 0

    def test_disabled_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            config.maintenance.passive_feedback_enabled = False

            result = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            assert isinstance(result, PassiveFeedbackResult)
            assert result.feedback_written == 0
            assert result.inline_processed == 0


# ---------------------------------------------------------------------------
# Post-hoc path tests (mock LLM fn)
# ---------------------------------------------------------------------------

class TestDerivePostHocPath(unittest.TestCase):
    def _insert_retrieval_hit(
        self,
        conn: sqlite3.Connection,
        archive_id: str,
        note_id: str,
        hit_id: int = 1,
        query_id: int = 1,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO retrieval_queries
                (id, query, query_type, archive_id)
            VALUES (?, 'test query', 'semantic', ?)
            """,
            (query_id, archive_id),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO retrieval_hits
                (id, query_id, note_id, rank, channel, final_score)
            VALUES (?, ?, ?, 1, 'semantic', 0.9)
            """,
            (hit_id, query_id, note_id),
        )

    def test_post_hoc_writes_passive_used_likely_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-010")
                _insert_note(conn, "concept/retrieved")
                self._insert_retrieval_hit(conn, "arc-010", "concept/retrieved")
                # Promoted candidate same session, no attribution
                _insert_promotion_decision(
                    conn, 1, archive_id="arc-010", retrieval_attribution_json=None
                )

            result = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            conn2 = connect_db(config.paths.db_path)
            rows = conn2.execute(
                "SELECT * FROM note_passive_feedback WHERE note_id = 'concept/retrieved'"
            ).fetchall()
            conn2.close()

            assert len(rows) == 1
            assert rows[0]["feedback_state"] == "passive_used_likely"
            assert rows[0]["method"] == "maintenance_post_hoc"
            assert result.post_hoc_processed >= 1

    def test_post_hoc_handles_null_retrieval_hit_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-010-null-hit")
                _insert_note(conn, "concept/retrieved-null-hit")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO retrieval_queries
                        (id, query, query_type, archive_id)
                    VALUES (10, 'test query', 'semantic', 'arc-010-null-hit')
                    """,
                )
                conn.execute(
                    """
                    INSERT INTO retrieval_hits
                        (query_id, note_id, rank, channel, final_score)
                    VALUES (10, 'concept/retrieved-null-hit', 1, 'semantic', 0.9)
                    """,
                )
                _insert_promotion_decision(
                    conn, 10, archive_id="arc-010-null-hit", retrieval_attribution_json=None
                )

            result = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            conn2 = connect_db(config.paths.db_path)
            row = conn2.execute(
                """
                SELECT retrieval_hit_id, feedback_state
                FROM note_passive_feedback
                WHERE note_id = 'concept/retrieved-null-hit'
                """
            ).fetchone()
            conn2.close()

            assert row is not None
            assert row["retrieval_hit_id"] is None
            assert row["feedback_state"] == "passive_used_likely"
            assert result.post_hoc_processed >= 1

    def test_post_hoc_skipped_when_inline_covered(self) -> None:
        """When inline path already wrote a row for (note_id, session, state, method),
        post-hoc UNIQUE conflict causes INSERT OR IGNORE to skip — idempotent."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-011")
                _insert_note(conn, "concept/overlap")
                attribution = json.dumps([
                    {"note_id": "concept/overlap", "evidence_type": "paraphrase", "confidence": 0.7}
                ])
                self._insert_retrieval_hit(conn, "arc-011", "concept/overlap")
                _insert_promotion_decision(
                    conn, 1, archive_id="arc-011", retrieval_attribution_json=attribution
                )

            result = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            conn2 = connect_db(config.paths.db_path)
            rows = conn2.execute(
                "SELECT * FROM note_passive_feedback WHERE note_id = 'concept/overlap'"
            ).fetchall()
            conn2.close()

            # One row from inline, post-hoc skipped by UNIQUE constraint
            assert len(rows) == 1

    def test_post_hoc_with_mock_llm_misleading(self) -> None:
        """Mock LLM fn returning misleading label → passive_misleading_likely row.

        Requires a promoted note in the notes table for the same session so that
        promoted_summaries is non-empty and the LLM path is triggered.
        """
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            with conn:
                _insert_extraction_queue(conn, 1, "arc-012")
                # The retrieved note (what was retrieved before the session)
                _insert_note(conn, "concept/misleading-note")
                self._insert_retrieval_hit(conn, "arc-012", "concept/misleading-note", hit_id=2, query_id=2)
                # The promoted note (what the session produced), same archive_id
                _insert_note(conn, "concept/promoted-result")
                _insert_promotion_decision(
                    conn, 1,
                    archive_id="arc-012",
                    decision="new",
                    target_note_id="concept/promoted-result",
                    retrieval_attribution_json=None,
                )

            def _mock_llm_fn(*, retrieved_note_id: str, retrieved_summary: str, session_evidence: list) -> dict:
                return {"feedback_state": "passive_misleading_likely", "confidence": 0.75}

            result = derive_passive_feedback(conn, config, dry_run=False, llm_fn=_mock_llm_fn)
            conn.close()

            conn2 = connect_db(config.paths.db_path)
            rows = conn2.execute(
                "SELECT * FROM note_passive_feedback WHERE note_id = 'concept/misleading-note'"
            ).fetchall()
            conn2.close()

            assert len(rows) >= 1
            states = {r["feedback_state"] for r in rows}
            assert "passive_misleading_likely" in states

    def test_inline_only_config_skips_post_hoc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            config, conn = _setup_db(ws)
            config.maintenance.passive_feedback_inline_only = True
            with conn:
                _insert_extraction_queue(conn, 1, "arc-013")
                _insert_note(conn, "concept/inline-only")
                self._insert_retrieval_hit(conn, "arc-013", "concept/inline-only", hit_id=3, query_id=3)
                _insert_promotion_decision(
                    conn, 1, archive_id="arc-013", retrieval_attribution_json=None
                )

            result = derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            assert result.post_hoc_processed == 0


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
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
from second_brain.promotion.auto_resolver import (
    AutoResolveRunResult,
    _append_llm_analysis,
    _load_candidate_payload,
    _make_llm_fn,
    _parse_llm_response,
    _resolve_low_confidence,
    run_auto_resolve,
)
from second_brain.promotion.review_ops import ReviewItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _config(workspace: Path):
    _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
    _write(workspace / "brain/system/USER.md", "# USER\n")
    _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
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

        [extraction]
        vlm_provider = "fixture"
        vlm_model = "fixture-vlm"
        llm_provider = "fixture"
        llm_model = "fixture-llm"
        max_queue_batch = 5
        """,
    )
    cfg = load_config(config_path, workspace)
    conn = connect_db(cfg.paths.db_path)
    run_migrations(conn, ROOT / "migrations")
    conn.close()
    return cfg


def _clean_payload(*, title: str = "Test Concept", note_type: str = "concept",
                   confidence: str = "low") -> dict:
    """A minimal gate-passing candidate payload."""
    return {
        "schema_version": "mnemos-extraction-v1",
        "type": note_type,
        "title": title,
        "summary": f"Summary of {title}",
        "body": f"Body content for {title}.",
        "confidence": confidence,
        "source_refs": ["archive:test-archive-001"],
        "evidence": [{"id": "win-1", "window_id": "win-1"}],
        "components": [
            {
                "component_id": "c1",
                "component_type": "definition",
                "text": f"A definition of {title}.",
                "evidence": [{"id": "win-1"}],
            }
        ],
        "temporal_order": ["c1"],
        "traces": [],
    }


def _seed_candidate(conn, *, title: str = "Test Concept", note_type: str = "concept",
                    confidence: str = "low", promotion_state: str = "review_routed") -> int:
    """Insert extraction_queue + extraction_candidates row. Returns candidate_id."""
    payload = _clean_payload(title=title, note_type=note_type, confidence=confidence)
    with conn:
        conn.execute(
            """
            INSERT INTO extraction_queue
                (source_kind, archive_id, status, duplicate_state, projection_metadata_json)
            VALUES ('artifact', 'test-archive-001', 'processed', NULL, '{}')
            """
        )
        queue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO extraction_candidates
                (queue_id, candidate_index, candidate_type, title,
                 payload_json, validation_status, promotion_state,
                 model_provider, model_name)
            VALUES (?, 0, ?, ?, ?, 'promotion_ready', ?, 'fixture', 'fixture-llm')
            """,
            (queue_id, note_type, title, json.dumps(payload), promotion_state),
        )
        cid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        # Insert promotion_decisions row marking it review_routed
        conn.execute(
            """
            INSERT OR IGNORE INTO promotion_decisions
                (candidate_id, decision, target_note_id, archive_id,
                 check_errors_json, promotion_state, bridge_id)
            VALUES (?, 'review', ?, 'test-archive-001', '[]', 'review_routed',
                    'job:process-promotion-queue')
            """,
            (cid, f"{note_type}/{title.lower().replace(' ', '-')}"),
        )
        return cid


def _make_review_file(
    review_dir: Path,
    *,
    slug: str,
    note_id: str,
    candidate_id: int,
    note_type: str = "concept",
    title: str = "Test Concept",
    reason: str = "wiki_conflict",
    body: str = "Some content here.",
) -> Path:
    review_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}-2026-05-18-00-00-00.md"
    path = review_dir / filename
    content = (
        f"---\n"
        f"id: {note_id}\n"
        f"type: {note_type}\n"
        f"title: {title}\n"
        f"lifecycle_state: review\n"
        f"created_at: '2026-05-18T00:00:00Z'\n"
        f"updated_at: '2026-05-18T00:00:00Z'\n"
        f"corroboration_count: 1\n"
        f"source_refs:\n"
        f"- candidate:{candidate_id}\n"
        f"---\n\n"
        f"## Promotion Review\n\n"
        f"Reason: {reason}\n\n"
        f"## Summary\n\n"
        f"{body}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _approve_llm(prompt: str) -> str:  # noqa: ARG001
    return '{"action": "approve", "reasoning": "good candidate", "confidence": 0.90}'


def _reject_llm(prompt: str) -> str:  # noqa: ARG001
    return '{"action": "reject", "reasoning": "duplicate content", "confidence": 0.85}'


def _human_llm(prompt: str) -> str:  # noqa: ARG001
    return '{"action": "human", "reasoning": "cannot determine", "confidence": 0.40}'


def _low_conf_llm(prompt: str) -> str:  # noqa: ARG001
    return '{"action": "approve", "reasoning": "ok", "confidence": 0.60}'


# ---------------------------------------------------------------------------
# Unit tests: _parse_llm_response
# ---------------------------------------------------------------------------

class ParseLLMResponseTest(unittest.TestCase):

    def test_valid_json(self):
        result = _parse_llm_response('{"action": "approve", "confidence": 0.9, "reasoning": "ok"}')
        self.assertEqual(result["action"], "approve")
        self.assertAlmostEqual(result["confidence"], 0.9)

    def test_markdown_fences_stripped(self):
        raw = '```json\n{"action": "reject", "confidence": 0.8, "reasoning": "dup"}\n```'
        result = _parse_llm_response(raw)
        self.assertEqual(result["action"], "reject")

    def test_invalid_json_returns_human(self):
        result = _parse_llm_response("not json at all")
        self.assertEqual(result["action"], "human")
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["reasoning"], "parse_error")

    def test_non_dict_returns_human(self):
        result = _parse_llm_response("[1, 2, 3]")
        self.assertEqual(result["action"], "human")

    def test_empty_string_returns_human(self):
        result = _parse_llm_response("")
        self.assertEqual(result["action"], "human")


# ---------------------------------------------------------------------------
# Unit tests: _append_llm_analysis
# ---------------------------------------------------------------------------

class AppendLLMAnalysisTest(unittest.TestCase):

    def _make_item(self, path: Path) -> ReviewItem:
        return ReviewItem(
            filename=path.name,
            path=path,
            note_id="concept/test",
            title="Test",
            note_type="concept",
            reason="wiki_conflict",
            created_at="2026-05-18T00:00:00Z",
        )

    def test_appends_analysis_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "review.md"
            p.write_text("---\nid: test\n---\n\n## Body\n\nContent.", encoding="utf-8")
            item = self._make_item(p)
            _append_llm_analysis(item, {"action": "human", "confidence": 0.4, "reasoning": "ambiguous"})
            text = p.read_text()
            self.assertIn("## LLM Analysis", text)
            self.assertIn("**Action suggested:** human", text)
            self.assertIn("**Confidence:** 0.40", text)
            self.assertIn("**Reasoning:** ambiguous", text)

    def test_idempotent_second_call_no_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "review.md"
            p.write_text("---\nid: test\n---\n\n## Body\n\nContent.", encoding="utf-8")
            item = self._make_item(p)
            _append_llm_analysis(item, {"action": "human", "confidence": 0.4, "reasoning": "ambiguous"})
            _append_llm_analysis(item, {"action": "human", "confidence": 0.4, "reasoning": "ambiguous"})
            text = p.read_text()
            self.assertEqual(text.count("## LLM Analysis"), 1)

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nonexistent.md"
            item = self._make_item(p)
            _append_llm_analysis(item, {"action": "human", "confidence": 0.4, "reasoning": "x"})
            # Should not raise


# ---------------------------------------------------------------------------
# Unit tests: _make_llm_fn
# ---------------------------------------------------------------------------

class MakeLLMFnTest(unittest.TestCase):

    def test_fixture_provider_returns_callable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp))
            fn = _make_llm_fn(cfg)
            self.assertTrue(callable(fn))

    def test_fixture_provider_returns_json_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp))
            fn = _make_llm_fn(cfg)
            result = fn("any prompt")
            parsed = json.loads(result)
            self.assertIn("action", parsed)


# ---------------------------------------------------------------------------
# Integration tests: run_auto_resolve
# ---------------------------------------------------------------------------

class AutoResolverIntegrationTest(unittest.TestCase):

    def test_empty_review_dir_returns_zero_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp))
            result = run_auto_resolve(cfg, llm_fn=_approve_llm, dry_run=True)
            self.assertIsInstance(result, AutoResolveRunResult)
            self.assertEqual(result.approved, 0)
            self.assertEqual(result.rejected, 0)
            self.assertEqual(result.human_fallback, 0)

    def test_missing_candidate_payload_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp))
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            # Review file references candidate:9999 which doesn't exist in DB
            _make_review_file(
                review_dir,
                slug="orphaned-note",
                note_id="concept/orphaned-note",
                candidate_id=9999,
                reason="wiki_conflict",
            )
            result = run_auto_resolve(cfg, llm_fn=_approve_llm, dry_run=False)
            self.assertEqual(len(result.errors), 1)
            self.assertIn("candidate_payload_not_found", result.errors[0])

    def test_llm_approve_removes_review_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = _config(workspace)
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            cid = _seed_candidate(conn)
            conn.close()

            review_dir = cfg.paths.vault_root / "wiki" / "review"
            rf = _make_review_file(
                review_dir,
                slug="my-concept",
                note_id="concept/my-concept",
                candidate_id=cid,
                reason="low_confidence",
            )
            result = run_auto_resolve(cfg, llm_fn=_approve_llm, dry_run=False)
            self.assertEqual(result.approved, 1)
            self.assertFalse(rf.exists(), "review file should be deleted after approve")

    def test_llm_reject_removes_review_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = _config(workspace)
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            cid = _seed_candidate(conn)
            conn.close()

            review_dir = cfg.paths.vault_root / "wiki" / "review"
            rf = _make_review_file(
                review_dir,
                slug="my-concept",
                note_id="concept/my-concept",
                candidate_id=cid,
                reason="wiki_conflict",
            )
            result = run_auto_resolve(cfg, llm_fn=_reject_llm, dry_run=False)
            self.assertEqual(result.rejected, 1)
            self.assertFalse(rf.exists(), "review file should be deleted after reject")

    def test_low_confidence_llm_writes_human_fallback(self):
        """LLM returns approve but confidence < 0.75 → human fallback, file kept."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = _config(workspace)
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            cid = _seed_candidate(conn)
            conn.close()

            review_dir = cfg.paths.vault_root / "wiki" / "review"
            rf = _make_review_file(
                review_dir,
                slug="my-concept",
                note_id="concept/my-concept",
                candidate_id=cid,
                reason="low_confidence",
            )
            result = run_auto_resolve(cfg, llm_fn=_low_conf_llm, dry_run=False)
            self.assertEqual(result.human_fallback, 1)
            self.assertTrue(rf.exists(), "review file should remain for human")
            text = rf.read_text()
            self.assertIn("## LLM Analysis", text)

    def test_human_action_writes_fallback(self):
        """LLM returns action=human → fallback with LLM Analysis block."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = _config(workspace)
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            cid = _seed_candidate(conn)
            conn.close()

            review_dir = cfg.paths.vault_root / "wiki" / "review"
            rf = _make_review_file(
                review_dir,
                slug="my-concept",
                note_id="concept/my-concept",
                candidate_id=cid,
                reason="contested_lifecycle",
            )
            result = run_auto_resolve(cfg, llm_fn=_human_llm, dry_run=False)
            self.assertEqual(result.human_fallback, 1)
            self.assertIn("## LLM Analysis", rf.read_text())

    def test_idempotent_fallback_append(self):
        """Running twice on same file doesn't duplicate the LLM Analysis block."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = _config(workspace)
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            cid = _seed_candidate(conn)
            conn.close()

            review_dir = cfg.paths.vault_root / "wiki" / "review"
            rf = _make_review_file(
                review_dir,
                slug="my-concept",
                note_id="concept/my-concept",
                candidate_id=cid,
                reason="wiki_conflict",
            )
            run_auto_resolve(cfg, llm_fn=_human_llm, dry_run=False)
            run_auto_resolve(cfg, llm_fn=_human_llm, dry_run=False)
            text = rf.read_text()
            self.assertEqual(text.count("## LLM Analysis"), 1)

    def test_dry_run_makes_no_changes(self):
        """dry_run=True → no files changed, no DB writes."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = _config(workspace)
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            cid = _seed_candidate(conn)
            conn.close()

            review_dir = cfg.paths.vault_root / "wiki" / "review"
            rf = _make_review_file(
                review_dir,
                slug="my-concept",
                note_id="concept/my-concept",
                candidate_id=cid,
                reason="low_confidence",
            )
            original_text = rf.read_text()
            result = run_auto_resolve(cfg, llm_fn=_approve_llm, dry_run=True)
            # approve counted but no file changes
            self.assertEqual(result.approved, 1)
            self.assertTrue(rf.exists())
            self.assertEqual(rf.read_text(), original_text)

    def test_gate_fail_rejects_immediately(self):
        """Gate-failing payload → rejected without LLM call."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = _config(workspace)
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")

            # Seed a candidate with an invalid payload (missing required type)
            with conn:
                conn.execute(
                    "INSERT INTO extraction_queue "
                    "(source_kind, archive_id, status, duplicate_state, projection_metadata_json) "
                    "VALUES ('artifact', 'test-001', 'processed', NULL, '{}')"
                )
                queue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                # payload with unsupported type fails gate
                bad_payload = {
                    "schema_version": "mnemos-extraction-v1",
                    "type": "unsupported_type_xyz",
                    "title": "Bad Candidate",
                    "summary": "x", "body": "x", "confidence": "low",
                    "source_refs": [], "evidence": [], "components": [], "traces": [],
                    "temporal_order": [],
                }
                conn.execute(
                    "INSERT INTO extraction_candidates "
                    "(queue_id, candidate_index, candidate_type, title, payload_json, "
                    "validation_status, promotion_state, model_provider, model_name) "
                    "VALUES (?, 0, 'concept', 'Bad Candidate', ?, 'promotion_ready', "
                    "'review_routed', 'fixture', 'fixture-llm')",
                    (queue_id, json.dumps(bad_payload)),
                )
                cid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    "INSERT OR IGNORE INTO promotion_decisions "
                    "(candidate_id, decision, target_note_id, archive_id, check_errors_json, "
                    "promotion_state, bridge_id) "
                    "VALUES (?, 'review', 'concept/bad-candidate', 'test-001', '[]', "
                    "'review_routed', 'job:process-promotion-queue')",
                    (cid,),
                )
            conn.close()

            llm_called = []

            def tracking_llm(prompt: str) -> str:
                llm_called.append(prompt)
                return '{"action": "approve", "confidence": 0.9, "reasoning": "ok"}'

            review_dir = cfg.paths.vault_root / "wiki" / "review"
            _make_review_file(
                review_dir,
                slug="bad-candidate",
                note_id="concept/bad-candidate",
                candidate_id=cid,
                reason="low_confidence",
            )
            result = run_auto_resolve(cfg, llm_fn=tracking_llm, dry_run=False)
            self.assertEqual(result.rejected, 1)
            self.assertEqual(len(llm_called), 0, "LLM should not be called when gate fails")

    def test_gate_fail_dry_run(self):
        """Gate-failing payload dry_run → rejected counted but review file kept."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = _config(workspace)
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")

            with conn:
                conn.execute(
                    "INSERT INTO extraction_queue "
                    "(source_kind, archive_id, status, duplicate_state, projection_metadata_json) "
                    "VALUES ('artifact', 'test-001', 'processed', NULL, '{}')"
                )
                queue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                bad_payload = {
                    "schema_version": "mnemos-extraction-v1",
                    "type": "unsupported_type_xyz",
                    "title": "Bad Candidate",
                    "summary": "x", "body": "x", "confidence": "low",
                    "source_refs": [], "evidence": [], "components": [], "traces": [],
                    "temporal_order": [],
                }
                conn.execute(
                    "INSERT INTO extraction_candidates "
                    "(queue_id, candidate_index, candidate_type, title, payload_json, "
                    "validation_status, promotion_state, model_provider, model_name) "
                    "VALUES (?, 0, 'concept', 'Bad Candidate', ?, 'promotion_ready', "
                    "'review_routed', 'fixture', 'fixture-llm')",
                    (queue_id, json.dumps(bad_payload)),
                )
                cid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    "INSERT OR IGNORE INTO promotion_decisions "
                    "(candidate_id, decision, target_note_id, archive_id, check_errors_json, "
                    "promotion_state, bridge_id) "
                    "VALUES (?, 'review', 'concept/bad-candidate', 'test-001', '[]', "
                    "'review_routed', 'job:process-promotion-queue')",
                    (cid,),
                )
            conn.close()

            review_dir = cfg.paths.vault_root / "wiki" / "review"
            rf = _make_review_file(
                review_dir,
                slug="bad-candidate",
                note_id="concept/bad-candidate",
                candidate_id=cid,
                reason="low_confidence",
            )
            result = run_auto_resolve(cfg, llm_fn=_approve_llm, dry_run=True)
            self.assertEqual(result.rejected, 1)
            self.assertTrue(rf.exists(), "dry_run should not delete review file")


# ---------------------------------------------------------------------------
# Unit tests: resolver functions with injected llm_fn
# ---------------------------------------------------------------------------

class ResolverUnitTest(unittest.TestCase):

    def test_resolve_low_confidence_approve(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp))
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            payload = {"type": "concept", "title": "Foo", "body": "body", "summary": "sum"}
            result = _resolve_low_confidence(payload, conn, cfg, _approve_llm)
            conn.close()
            self.assertEqual(result["action"], "approve")

    def test_resolve_low_confidence_parse_error_returns_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp))
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            payload = {"type": "concept", "title": "Foo", "body": "body", "summary": "sum"}
            result = _resolve_low_confidence(payload, conn, cfg, lambda _: "not json")
            conn.close()
            self.assertEqual(result["action"], "human")
            self.assertEqual(result["reasoning"], "parse_error")

    def test_resolve_low_confidence_llm_exception_returns_human(self):
        def _exploding_llm(prompt: str) -> str:
            raise RuntimeError("connection refused")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp))
            conn = connect_db(cfg.paths.db_path)
            run_migrations(conn, ROOT / "migrations")
            payload = {"type": "concept", "title": "Foo", "body": "body", "summary": "sum"}
            result = _resolve_low_confidence(payload, conn, cfg, _exploding_llm)
            conn.close()
            self.assertEqual(result["action"], "human")
            self.assertIn("llm_error", result["reasoning"])


if __name__ == "__main__":
    unittest.main()

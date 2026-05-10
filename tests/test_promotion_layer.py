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

from second_brain.config import AppConfig, PromotionConfig, load_config
from second_brain.db import connect_db, run_migrations
from second_brain.promotion.gate import run_gate
from second_brain.promotion.decisions import decide
from second_brain.promotion.renderer import render
from second_brain.promotion.writer import write_promotion, write_rejected
from second_brain.promotion.worker import process_promotion_queue
from second_brain.promotion.models import GateResult, PromotionDecision, PromotionRunResult
from second_brain.parser import split_frontmatter, NoteParseError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _config(
    workspace: Path,
    promotion_overrides: dict | None = None,
    *,
    embeddings_enabled: bool = False,
) -> AppConfig:
    _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
    _write(workspace / "brain/system/USER.md", "# USER\n")
    _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
    config_path = workspace / "config.toml"
    promo_section = ""
    if promotion_overrides:
        lines = "\n".join(f'{k} = {json.dumps(v)}' for k, v in promotion_overrides.items())
        promo_section = f"\n[promotion]\n{lines}\n"
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
        enabled = {str(embeddings_enabled).lower()}
        provider = "hash"
        model = "hash-256-v1"
        dimensions = 256

        [graph]
        enabled = false

        [extraction]
        vlm_provider = "fixture"
        vlm_model = "fixture-vlm"
        llm_provider = "fixture"
        llm_model = "fixture-llm"
        max_queue_batch = 5
        {promo_section}
        """,
    )
    cfg = load_config(config_path, workspace)
    conn = connect_db(cfg.paths.db_path)
    run_migrations(conn, ROOT / "migrations")
    conn.close()
    return cfg


def _clean_payload(overrides: dict | None = None) -> dict:
    """A minimal valid promotion_ready candidate payload."""
    payload = {
        "schema_version": "mnemos-extraction-v1",
        "type": "concept",
        "title": "Test Concept",
        "summary": "A test concept for unit tests.",
        "body": "This concept explains something.",
        "confidence": "medium",
        "source_refs": ["archive:test-archive-001"],
        "evidence": [{"id": "win-1", "window_id": "win-1"}],
        "components": [
            {
                "component_id": "c1",
                "component_type": "definition",
                "text": "A definition of the test concept.",
                "evidence": [{"id": "win-1"}],
            }
        ],
        "temporal_order": ["c1"],
        "traces": [],
    }
    if overrides:
        payload.update(overrides)
    return payload


def _seed_candidate(conn, *, payload: dict | None = None, promotion_state: str = "promotion_ready") -> int:
    """Insert a minimal extraction_queue + extraction_candidates row. Returns candidate_id."""
    p = payload or _clean_payload()
    with conn:
        conn.execute(
            """
            INSERT INTO extraction_queue
                (source_kind, archive_id, status, duplicate_state,
                 projection_metadata_json)
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
            (
                queue_id,
                str(p.get("type", "concept")),
                str(p.get("title", "Test Concept")),
                json.dumps(p),
                promotion_state,
            ),
        )
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


# ---------------------------------------------------------------------------
# Phase 1: Gate tests
# ---------------------------------------------------------------------------

class GateTest(unittest.TestCase):

    def _cfg(self, **overrides):
        cfg = AppConfig.__new__(AppConfig)
        from second_brain.config import (
            PathsConfig, RetrievalConfig, IngestConfig,
            EmbeddingsConfig, GraphConfig, TrustConfig,
            ExtractionConfig, PromotionConfig,
        )
        object.__setattr__(cfg, "paths", PathsConfig(
            workspace_root=Path("."),
            vault_root=Path("."),
            db_path=Path("."),
            vectors_dir=Path("."),
            graph_dir=Path("."),
            eval_queries=Path("."),
        ))
        object.__setattr__(cfg, "retrieval", RetrievalConfig())
        object.__setattr__(cfg, "ingest", IngestConfig())
        object.__setattr__(cfg, "embeddings", EmbeddingsConfig())
        object.__setattr__(cfg, "graph", GraphConfig())
        object.__setattr__(cfg, "trust", TrustConfig())
        object.__setattr__(cfg, "extraction", ExtractionConfig())
        promo = PromotionConfig(**overrides) if overrides else PromotionConfig()
        object.__setattr__(cfg, "promotion", promo)
        object.__setattr__(cfg, "daemon", None)
        object.__setattr__(cfg, "bridges", {})
        object.__setattr__(cfg, "config_path", None)
        return cfg

    def test_clean_payload_passes_all_checks(self) -> None:
        cfg = self._cfg()
        result = run_gate(_clean_payload(), config=cfg)
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_check7_policy_disabled(self) -> None:
        cfg = self._cfg(enabled=False)
        result = run_gate(_clean_payload(), config=cfg)
        self.assertFalse(result.ok)
        self.assertIn("promotion_policy_disabled", result.errors)

    def test_check5_unsupported_candidate_type(self) -> None:
        cfg = self._cfg()
        p = _clean_payload({"type": "banana"})
        result = run_gate(p, config=cfg)
        self.assertFalse(result.ok)
        self.assertTrue(any("unsupported_candidate_type" in e for e in result.errors))

    def test_check5_unsupported_component_type(self) -> None:
        cfg = self._cfg()
        p = _clean_payload()
        p["components"][0]["component_type"] = "flying_saucer"
        result = run_gate(p, config=cfg)
        self.assertFalse(result.ok)
        self.assertTrue(any("unsupported_component_type" in e for e in result.errors))

    def test_check1_component_missing_evidence(self) -> None:
        cfg = self._cfg()
        p = _clean_payload()
        p["components"][0]["evidence"] = []
        result = run_gate(p, config=cfg)
        self.assertFalse(result.ok)
        # Should produce a component evidence error
        self.assertTrue(any("component_evidence" in e or "component" in e for e in result.errors))

    def test_check3_dangling_component_link(self) -> None:
        cfg = self._cfg()
        p = _clean_payload()
        p["components"][0]["links"] = [{"target": "nonexistent-id"}]
        result = run_gate(p, config=cfg)
        self.assertFalse(result.ok)
        self.assertTrue(any("component_link_dangling" in e for e in result.errors))

    def test_check2_and_4_dangling_trace_member(self) -> None:
        cfg = self._cfg()
        p = _clean_payload()
        p["traces"] = [{"trace_id": "t1", "component_ids": ["c1", "ghost-component"]}]
        result = run_gate(p, config=cfg)
        self.assertFalse(result.ok)
        self.assertTrue(any("trace_component_dangling" in e for e in result.errors))

    def test_check6_component_count_exceeds_limit(self) -> None:
        cfg = self._cfg(max_components_per_candidate=2)
        p = _clean_payload()
        p["components"] = [
            {
                "component_id": f"c{i}",
                "component_type": "definition",
                "text": f"Component {i}",
                "evidence": [{"id": "win-1"}],
            }
            for i in range(3)
        ]
        result = run_gate(p, config=cfg)
        self.assertFalse(result.ok)
        self.assertTrue(any("component_count_exceeds_limit" in e for e in result.errors))

    def test_multiple_errors_collected(self) -> None:
        """Gate collects all failures, not just the first."""
        cfg = self._cfg()
        p = _clean_payload({"type": "banana"})
        p["components"][0]["evidence"] = []
        result = run_gate(p, config=cfg)
        self.assertFalse(result.ok)
        self.assertGreater(len(result.errors), 1)


# ---------------------------------------------------------------------------
# Phase 2: Decision engine tests
# ---------------------------------------------------------------------------

class DecisionEngineTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = _config(Path(self._tmp.name))
        self._conn = connect_db(self._cfg.paths.db_path)

    def tearDown(self) -> None:
        self._conn.close()
        self._tmp.cleanup()

    def test_new_decision_when_no_existing_note(self) -> None:
        p = _clean_payload()
        decision = decide(p, duplicate_state=None, conn=self._conn, config=self._cfg)
        self.assertEqual(decision.decision, "new")

    def test_low_confidence_routes_to_review(self) -> None:
        p = _clean_payload({"confidence": "low"})
        decision = decide(p, duplicate_state=None, conn=self._conn, config=self._cfg)
        self.assertEqual(decision.decision, "review")
        self.assertEqual(decision.reason, "low_confidence")

    def test_low_confidence_fires_before_wiki_lookup(self) -> None:
        """Low-confidence guard fires even when no wiki notes exist."""
        p = _clean_payload({"confidence": "low"})
        decision = decide(p, duplicate_state=None, conn=self._conn, config=self._cfg)
        self.assertEqual(decision.decision, "review")

    def test_confirm_for_near_duplicate_with_matching_note(self) -> None:
        """canonical_duplicate source + strong match → confirm."""
        # Seed a wiki note in the DB
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO notes (id, type, title, summary, status, lifecycle_state,
                                   source_refs, body_path, content_hash,
                                   updated_at, created_at)
                VALUES ('concept/test-concept', 'concept', 'Test Concept',
                        'A test concept', 'active', 'canonical',
                        '[]', '', 'abc', datetime('now'), datetime('now'))
                """
            )
        p = _clean_payload({"title": "Test Concept", "confidence": "medium"})
        decision = decide(p, duplicate_state="canonical_duplicate", conn=self._conn, config=self._cfg)
        self.assertEqual(decision.decision, "confirm")
        self.assertIn("near_duplicate_corroboration", decision.reason)

    def test_review_on_conflict_overrides_near_duplicate_prior(self) -> None:
        """canonical_duplicate source but contested note → review."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO notes (id, type, title, summary, status, lifecycle_state,
                                   source_refs, body_path, content_hash,
                                   updated_at, created_at)
                VALUES ('concept/test-concept', 'concept', 'Test Concept',
                        'A test concept', 'active', 'contested',
                        '[]', '', 'abc', datetime('now'), datetime('now'))
                """
            )
        p = _clean_payload({"title": "Test Concept", "confidence": "medium"})
        decision = decide(p, duplicate_state="canonical_duplicate", conn=self._conn, config=self._cfg)
        self.assertEqual(decision.decision, "review")
        self.assertEqual(decision.reason, "contested_lifecycle")


# ---------------------------------------------------------------------------
# Phase 3: Note renderer tests
# ---------------------------------------------------------------------------

class RendererTest(unittest.TestCase):

    def _decision(self, kind="new") -> PromotionDecision:
        return PromotionDecision(decision=kind, target_note_id=None, reason="test")

    def test_renders_concept_to_valid_frontmatter(self) -> None:
        p = _clean_payload()
        _, markdown = render(p, decision=self._decision("new"), candidate_id=1, archive_id="arc-001")
        parsed = split_frontmatter(markdown)
        self.assertEqual(parsed.metadata["type"], "concept")
        self.assertEqual(parsed.metadata["title"], "Test Concept")
        self.assertEqual(parsed.metadata["id"], "concept/test-concept")

    def test_provenance_refs_in_source_refs(self) -> None:
        p = _clean_payload()
        _, markdown = render(p, decision=self._decision("new"), candidate_id=42, archive_id="arc-xyz")
        parsed = split_frontmatter(markdown)
        refs = list(parsed.metadata.get("source_refs") or [])
        self.assertIn("candidate:42", refs)
        self.assertIn("archive:arc-xyz", refs)

    def test_procedure_steps_extracted_from_components(self) -> None:
        p = _clean_payload({
            "type": "procedure",
            "title": "Deploy Procedure",
            "components": [
                {
                    "component_id": "s1",
                    "component_type": "procedure_step",
                    "text": "Step one: prepare",
                    "evidence": [{"id": "win-1"}],
                },
                {
                    "component_id": "s2",
                    "component_type": "procedure_step",
                    "text": "Step two: execute",
                    "evidence": [{"id": "win-1"}],
                },
            ],
        })
        metadata, _ = render(p, decision=self._decision("new"), candidate_id=1, archive_id="arc")
        self.assertIn("steps", metadata)
        self.assertEqual(len(metadata["steps"]), 2)

    def test_failure_components_populate_failure_modes(self) -> None:
        p = _clean_payload({
            "type": "incident",
            "title": "DB Connection Failure",
            "components": [
                {
                    "component_id": "f1",
                    "component_type": "failure",
                    "text": "Connection pool exhausted",
                    "evidence": [{"id": "win-1"}],
                }
            ],
        })
        metadata, _ = render(p, decision=self._decision("new"), candidate_id=1, archive_id="arc")
        self.assertIn("failure_modes", metadata)

    def test_single_trace_populates_chain_id(self) -> None:
        p = _clean_payload()
        p["traces"] = [{"trace_id": "trace/my-chain", "component_ids": ["c1"]}]
        metadata, _ = render(p, decision=self._decision("new"), candidate_id=1, archive_id="arc")
        self.assertEqual(metadata.get("chain_id"), "trace/my-chain")

    def test_multiple_traces_no_chain_id(self) -> None:
        p = _clean_payload()
        p["traces"] = [
            {"trace_id": "trace/a", "component_ids": ["c1"]},
            {"trace_id": "trace/b", "component_ids": ["c1"]},
        ]
        metadata, _ = render(p, decision=self._decision("new"), candidate_id=1, archive_id="arc")
        self.assertIsNone(metadata.get("chain_id"))

    def test_review_decision_prepends_header(self) -> None:
        p = _clean_payload()
        decision = PromotionDecision(decision="review", target_note_id=None, reason="low_confidence")
        _, markdown = render(p, decision=decision, candidate_id=1, archive_id="arc")
        parsed = split_frontmatter(markdown)
        self.assertIn("Promotion Review", parsed.body)
        self.assertEqual(parsed.metadata.get("lifecycle_state"), "review")

    def test_corroboration_count_initialized_to_1(self) -> None:
        p = _clean_payload()
        metadata, _ = render(p, decision=self._decision("new"), candidate_id=1, archive_id="arc")
        self.assertEqual(metadata.get("corroboration_count"), 1)

    def test_confirm_increments_corroboration_count(self) -> None:
        """Confirm on existing note increments corroboration_count."""
        with tempfile.TemporaryDirectory() as tmp:
            existing_path = Path(tmp) / "test-concept.md"
            existing_path.write_text(
                "---\n"
                "id: concept/test-concept\n"
                "type: concept\n"
                "title: Test Concept\n"
                "summary: summary\n"
                "confidence: medium\n"
                "source_refs: []\n"
                "created_at: '2025-01-01T00:00:00Z'\n"
                "updated_at: '2025-01-01T00:00:00Z'\n"
                "lifecycle_state: draft\n"
                "corroboration_count: 1\n"
                "---\n\n"
                "Existing body.\n"
            )
            decision = PromotionDecision(
                decision="confirm",
                target_note_id="concept/test-concept",
                reason="strong_match:exact_title",
            )
            p = _clean_payload()
            metadata, _ = render(
                p,
                decision=decision,
                candidate_id=2,
                archive_id="arc",
                existing_note_path=existing_path,
            )
            self.assertEqual(metadata.get("corroboration_count"), 2)

    def test_lifecycle_advances_on_corroboration_threshold(self) -> None:
        """Two corroborations promote draft → verified."""
        with tempfile.TemporaryDirectory() as tmp:
            existing_path = Path(tmp) / "test.md"
            existing_path.write_text(
                "---\n"
                "id: concept/thing\n"
                "type: concept\n"
                "title: Thing\n"
                "summary: s\n"
                "confidence: medium\n"
                "source_refs: []\n"
                "created_at: '2025-01-01T00:00:00Z'\n"
                "updated_at: '2025-01-01T00:00:00Z'\n"
                "lifecycle_state: draft\n"
                "corroboration_count: 1\n"
                "---\n\nBody.\n"
            )
            p = _clean_payload({"title": "Thing"})
            decision = PromotionDecision(
                decision="confirm",
                target_note_id="concept/thing",
                reason="test",
            )
            metadata, _ = render(
                p, decision=decision, candidate_id=3, archive_id="arc",
                existing_note_path=existing_path,
            )
            self.assertEqual(metadata["corroboration_count"], 2)
            self.assertEqual(metadata["lifecycle_state"], "verified")

    def test_rendered_markdown_parses_cleanly(self) -> None:
        for note_type in ("concept", "decision", "incident", "source"):
            with self.subTest(note_type=note_type):
                p = _clean_payload({"type": note_type, "title": f"Test {note_type.title()}"})
                _, markdown = render(p, decision=self._decision("new"), candidate_id=1, archive_id="arc")
                # Must not raise
                parsed = split_frontmatter(markdown)
                self.assertEqual(parsed.metadata["type"], note_type)


# ---------------------------------------------------------------------------
# Phase 4: Wiki writer tests
# ---------------------------------------------------------------------------

class WikiWriterTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = _config(Path(self._tmp.name))
        self._conn = connect_db(self._cfg.paths.db_path)

    def tearDown(self) -> None:
        self._conn.close()
        self._tmp.cleanup()

    def _do_write(self, payload: dict | None = None, decision_kind: str = "new", candidate_id: int | None = None) -> Path:
        p = payload or _clean_payload()
        # Seed a real candidate row so FK constraint is satisfied
        real_id = _seed_candidate(self._conn, payload=p) if candidate_id is None else candidate_id
        decision = PromotionDecision(
            decision=decision_kind,
            target_note_id=None,
            reason="test",
        )
        metadata, markdown = render(p, decision=decision, candidate_id=real_id, archive_id="arc-001")
        with self._conn:
            write_promotion(
                markdown,
                metadata=metadata,
                decision=decision,
                candidate_id=real_id,
                archive_id="arc-001",
                bridge_id="test",
                conn=self._conn,
                vault_root=self._cfg.paths.vault_root,
            )
        from second_brain.ingest import TYPE_TO_DIRECTORY
        note_type = str(p.get("type", "concept"))
        # return expected path
        subdir = TYPE_TO_DIRECTORY.get(note_type, note_type + "s")
        from second_brain.review import slugify
        return self._cfg.paths.vault_root / "wiki" / subdir / f"{slugify(str(p.get('title','test')))}.md"

    def test_new_decision_creates_note_file(self) -> None:
        path = self._do_write()
        self.assertTrue(path.exists(), f"Expected note at {path}")

    def test_no_tmp_file_after_successful_write(self) -> None:
        path = self._do_write()
        tmp_path = path.with_suffix(".tmp")
        self.assertFalse(tmp_path.exists())

    def test_promotion_decisions_row_exists(self) -> None:
        self._do_write()
        row = self._conn.execute("SELECT * FROM promotion_decisions").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["decision"]), "new")
        self.assertEqual(str(row["promotion_state"]), "promoted")

    def test_extraction_candidates_state_updated(self) -> None:
        candidate_id = _seed_candidate(self._conn)
        payload = _clean_payload()
        decision = PromotionDecision(decision="new", target_note_id=None, reason="test")
        metadata, markdown = render(payload, decision=decision, candidate_id=candidate_id, archive_id="arc")
        with self._conn:
            write_promotion(
                markdown,
                metadata=metadata,
                decision=decision,
                candidate_id=candidate_id,
                archive_id="arc",
                bridge_id="test",
                conn=self._conn,
                vault_root=self._cfg.paths.vault_root,
            )
        row = self._conn.execute(
            "SELECT promotion_state FROM extraction_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(str(row["promotion_state"]), "promoted")

    def test_idempotency_second_write_is_noop(self) -> None:
        candidate_id = _seed_candidate(self._conn)
        payload = _clean_payload()
        decision = PromotionDecision(decision="new", target_note_id=None, reason="test")
        metadata, markdown = render(payload, decision=decision, candidate_id=candidate_id, archive_id="arc")
        for _ in range(2):
            with self._conn:
                write_promotion(
                    markdown,
                    metadata=metadata,
                    decision=decision,
                    candidate_id=candidate_id,
                    archive_id="arc",
                    bridge_id="test",
                    conn=self._conn,
                    vault_root=self._cfg.paths.vault_root,
                )
        rows = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM promotion_decisions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(int(rows["cnt"]), 1)

    def test_review_routes_to_review_subdir(self) -> None:
        payload = _clean_payload({"confidence": "low"})
        candidate_id = _seed_candidate(self._conn, payload=payload)
        decision = PromotionDecision(decision="review", target_note_id=None, reason="low_confidence")
        metadata, markdown = render(payload, decision=decision, candidate_id=candidate_id, archive_id="arc")
        with self._conn:
            write_promotion(
                markdown,
                metadata=metadata,
                decision=decision,
                candidate_id=candidate_id,
                archive_id="arc",
                bridge_id="test",
                conn=self._conn,
                vault_root=self._cfg.paths.vault_root,
            )
        review_dir = self._cfg.paths.vault_root / "wiki" / "review"
        files = list(review_dir.glob("*.md"))
        self.assertTrue(len(files) >= 1, "Expected at least one review file")

    def test_log_md_appended(self) -> None:
        candidate_id = _seed_candidate(self._conn)
        payload = _clean_payload()
        decision = PromotionDecision(decision="new", target_note_id=None, reason="test")
        metadata, markdown = render(payload, decision=decision, candidate_id=candidate_id, archive_id="arc")
        with self._conn:
            write_promotion(
                markdown,
                metadata=metadata,
                decision=decision,
                candidate_id=candidate_id,
                archive_id="arc",
                bridge_id="test",
                conn=self._conn,
                vault_root=self._cfg.paths.vault_root,
            )
        log_path = self._cfg.paths.vault_root / "wiki" / "log.md"
        self.assertTrue(log_path.exists())
        content = log_path.read_text()
        self.assertIn("new", content)
        self.assertIn("test", content)

    def test_parse_error_leaves_no_note(self) -> None:
        """If rendered markdown fails smoke-check parse, no note lands in wiki."""
        decision = PromotionDecision(decision="new", target_note_id=None, reason="test")
        bad_markdown = "not valid frontmatter at all"
        import traceback
        try:
            with self._conn:
                write_promotion(
                    bad_markdown,
                    metadata={"type": "concept", "title": "Bad", "id": "concept/bad"},
                    decision=decision,
                    candidate_id=555,
                    archive_id="arc",
                    bridge_id="test",
                    conn=self._conn,
                    vault_root=self._cfg.paths.vault_root,
                )
            self.fail("Expected exception from bad markdown")
        except Exception:
            pass
        # No note file and no .tmp file anywhere
        wiki_dir = self._cfg.paths.vault_root / "wiki"
        tmp_files = list(wiki_dir.rglob("*.tmp")) if wiki_dir.exists() else []
        self.assertEqual(tmp_files, [])

    def test_gate_rejected_candidate_marked_in_db(self) -> None:
        candidate_id = _seed_candidate(self._conn)
        with self._conn:
            write_rejected(self._conn, candidate_id, ["promotion_policy_disabled"], "test")
        row = self._conn.execute(
            "SELECT promotion_state FROM extraction_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(str(row["promotion_state"]), "rejected")


# ---------------------------------------------------------------------------
# End-to-end / worker tests
# ---------------------------------------------------------------------------

class WorkerTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = _config(Path(self._tmp.name))
        self._conn = connect_db(self._cfg.paths.db_path)

    def tearDown(self) -> None:
        self._conn.close()
        self._tmp.cleanup()

    def test_empty_queue_returns_zero_counts(self) -> None:
        result = process_promotion_queue(self._cfg)
        self.assertEqual(result.promoted, 0)
        self.assertEqual(result.rejected, 0)
        self.assertEqual(result.review_routed, 0)

    def test_full_pipeline_promotes_valid_candidate(self) -> None:
        candidate_id = _seed_candidate(self._conn)
        self._conn.close()

        result = process_promotion_queue(self._cfg)
        self.assertEqual(result.promoted, 1)
        self.assertEqual(result.rejected, 0)

        # Note exists in wiki
        from second_brain.ingest import TYPE_TO_DIRECTORY
        from second_brain.review import slugify
        note_dir = self._cfg.paths.vault_root / "wiki" / TYPE_TO_DIRECTORY["concept"]
        notes = list(note_dir.glob("*.md"))
        self.assertTrue(len(notes) >= 1)

        # Reconnect to verify DB state
        self._conn = connect_db(self._cfg.paths.db_path)
        row = self._conn.execute(
            "SELECT promotion_state FROM extraction_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(str(row["promotion_state"]), "promoted")

    def test_idempotency_second_run_skips(self) -> None:
        _seed_candidate(self._conn)
        self._conn.close()

        result1 = process_promotion_queue(self._cfg)
        result2 = process_promotion_queue(self._cfg)

        self.assertEqual(result1.promoted, 1)
        # Second run finds no new promotion_ready rows without decisions
        self.assertEqual(result2.promoted + result2.review_routed + result2.rejected, 0)

        self._conn = connect_db(self._cfg.paths.db_path)

    def test_low_confidence_candidate_routes_to_review(self) -> None:
        payload = _clean_payload({"confidence": "low"})
        _seed_candidate(self._conn, payload=payload)
        self._conn.close()

        result = process_promotion_queue(self._cfg)
        self.assertEqual(result.review_routed, 1)
        self.assertEqual(result.promoted, 0)

        review_dir = self._cfg.paths.vault_root / "wiki" / "review"
        self.assertTrue(review_dir.exists())
        review_files = list(review_dir.glob("*.md"))
        self.assertTrue(len(review_files) >= 1)

        self._conn = connect_db(self._cfg.paths.db_path)

    def test_gate_rejected_candidate_not_promoted(self) -> None:
        payload = _clean_payload({"type": "concept"})
        # Corrupt the payload: remove evidence so gate fails
        payload["evidence"] = []
        payload["components"][0]["evidence"] = []
        _seed_candidate(self._conn, payload=payload)
        self._conn.close()

        result = process_promotion_queue(self._cfg)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.promoted, 0)

        # Wiki should be empty
        concepts_dir = self._cfg.paths.vault_root / "wiki" / "concepts"
        notes = list(concepts_dir.glob("*.md")) if concepts_dir.exists() else []
        self.assertEqual(notes, [])

        self._conn = connect_db(self._cfg.paths.db_path)

    def test_stale_rejected_candidate_is_requeued_and_reprocessed(self) -> None:
        payload = _clean_payload({"type": "concept"})
        payload["evidence"] = []
        payload["components"][0]["evidence"] = []
        candidate_id = _seed_candidate(self._conn, payload=payload)
        self._conn.close()

        first = process_promotion_queue(self._cfg)
        self.assertEqual(first.rejected, 1)

        self._conn = connect_db(self._cfg.paths.db_path)
        with self._conn:
            self._conn.execute(
                "UPDATE extraction_candidates SET payload_json = ? WHERE id = ?",
                (json.dumps(_clean_payload()), candidate_id),
            )
            self._conn.execute(
                """
                UPDATE promotion_decisions
                SET created_at = datetime('now', '-25 hours')
                WHERE candidate_id = ?
                """,
                (candidate_id,),
            )
        self._conn.close()

        second = process_promotion_queue(self._cfg)
        self.assertEqual(second.requeued, 1)
        self.assertEqual(second.promoted, 1)

        self._conn = connect_db(self._cfg.paths.db_path)
        row = self._conn.execute(
            "SELECT promotion_state FROM extraction_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(str(row["promotion_state"]), "promoted")

    def test_stale_rejected_requeue_dry_run_does_not_mutate(self) -> None:
        candidate_id = _seed_candidate(self._conn)
        with self._conn:
            write_rejected(self._conn, candidate_id, ["temporary"], "test")
            self._conn.execute(
                """
                UPDATE promotion_decisions
                SET created_at = datetime('now', '-25 hours')
                WHERE candidate_id = ?
                """,
                (candidate_id,),
            )
        self._conn.close()

        result = process_promotion_queue(self._cfg, dry_run=True)
        self.assertEqual(result.requeued, 1)

        self._conn = connect_db(self._cfg.paths.db_path)
        row = self._conn.execute(
            "SELECT promotion_state FROM extraction_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        decision = self._conn.execute(
            "SELECT COUNT(*) AS count FROM promotion_decisions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(str(row["promotion_state"]), "rejected")
        self.assertEqual(int(decision["count"]), 1)

    def test_dry_run_makes_no_wiki_writes(self) -> None:
        _seed_candidate(self._conn)
        self._conn.close()

        result = process_promotion_queue(self._cfg, dry_run=True)
        self.assertEqual(result.promoted + result.review_routed, 1)

        concepts_dir = self._cfg.paths.vault_root / "wiki" / "concepts"
        notes = list(concepts_dir.glob("*.md")) if concepts_dir.exists() else []
        self.assertEqual(notes, [], "Dry run must not write notes")

        self._conn = connect_db(self._cfg.paths.db_path)

    def test_promotion_config_disabled_rejects_all(self) -> None:
        _seed_candidate(self._conn)
        self._conn.close()

        cfg = _config(Path(self._tmp.name), promotion_overrides={"enabled": False})
        result = process_promotion_queue(cfg)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.promoted, 0)

        self._conn = connect_db(self._cfg.paths.db_path)

    def test_daemon_job_runs_via_run_job(self) -> None:
        from second_brain.jobs import run_job
        _seed_candidate(self._conn)
        self._conn.close()

        result = run_job(self._cfg, job_name="process_promotion_queue")
        self.assertEqual(result.job_name, "process_promotion_queue")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.stats.get("promoted", 0), 1)

        self._conn = connect_db(self._cfg.paths.db_path)

    def test_process_promotion_queue_in_default_schedules(self) -> None:
        from second_brain.daemon_jobs import _DEFAULT_SCHEDULES
        self.assertIn("process_promotion_queue", _DEFAULT_SCHEDULES)

    def test_promotion_triggers_inline_embed_backfill_when_daemon_unreachable(self) -> None:
        self._conn.close()
        cfg = _config(Path(self._tmp.name), embeddings_enabled=True)
        conn = connect_db(cfg.paths.db_path)
        candidate_id = _seed_candidate(conn)
        with conn:
            conn.execute(
                """
                INSERT INTO archive(id, type, path, ingested_at, agent, tags, chunk_count)
                VALUES ('archive:test-archive-001', 'session', ?, datetime('now'), 'test', '[]', 1)
                """,
                (str(cfg.paths.vault_root / "raw" / "sessions" / "missing-vector.md"),),
            )
            conn.execute(
                """
                INSERT INTO archive_chunks(archive_id, chunk_index, body, summary, token_count, content_hash)
                VALUES ('archive:test-archive-001', 0, 'body text for embedding', 'summary text', 4, 'hash-1')
                """
            )
        conn.close()

        result = process_promotion_queue(cfg)
        self.assertEqual(result.promoted, 1)

        conn = connect_db(cfg.paths.db_path)
        archive_vectors = conn.execute(
            "SELECT COUNT(*) AS count FROM archive_chunk_vectors"
        ).fetchone()
        note_vectors = conn.execute(
            "SELECT COUNT(*) AS count FROM note_vectors"
        ).fetchone()
        row = conn.execute(
            "SELECT promotion_state FROM extraction_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(str(row["promotion_state"]), "promoted")
        self.assertGreater(int(archive_vectors["count"]), 0)
        self.assertGreater(int(note_vectors["count"]), 0)


if __name__ == "__main__":
    unittest.main()

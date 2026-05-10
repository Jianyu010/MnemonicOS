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
from second_brain.extraction import process_extraction_queue
from second_brain.extraction.providers import FixtureLLMProvider
from second_brain.extraction.queue import enqueue_extraction, mark_finished, mark_processing, pending_items
from second_brain.extraction.validation import (
    parse_intra_source_relations,
    validate_intra_source_relations,
)
from second_brain.ingest import ingest_artifact
from second_brain.promotion.worker import apply_relation_to_note, process_promotion_queue
from second_brain.parser import split_frontmatter
from second_brain.review import slugify


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

        [ingest]
        auto_trigger_pipeline = false

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


def _minimal_payload(title: str, source_ref: str = "archive:test-001", win_id: str = "win-1") -> dict:
    return {
        "schema_version": "mnemos-extraction-v1",
        "type": "concept",
        "title": title,
        "summary": f"Summary for {title}.",
        "body": f"Body of {title}.",
        "confidence": "high",
        "source_refs": [source_ref],
        "evidence": [{"id": win_id}],
        "components": [
            {
                "component_id": "c1",
                "component_type": "definition",
                "text": f"Definition of {title}.",
                "evidence": [{"id": win_id}],
                "links": [],
            }
        ],
        "traces": [{"trace_id": "t1", "component_ids": ["c1"]}],
        "temporal_order": ["c1"],
    }


# ---------------------------------------------------------------------------
# Unit tests: validate_intra_source_relations
# ---------------------------------------------------------------------------

class ValidateIntraSourceRelationsTest(unittest.TestCase):
    def test_valid_entry(self):
        result = validate_intra_source_relations(
            [{"source_index": 0, "target_index": 1, "relation_type": "derived_from"}],
            candidate_count=2,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_index"], 0)
        self.assertEqual(result[0]["target_index"], 1)
        self.assertEqual(result[0]["relation_type"], "derived_from")

    def test_rationale_preserved(self):
        result = validate_intra_source_relations(
            [{"source_index": 0, "target_index": 1, "relation_type": "derived_from", "rationale": "because"}],
            candidate_count=2,
        )
        self.assertEqual(result[0].get("rationale"), "because")

    def test_self_loop_dropped(self):
        result = validate_intra_source_relations(
            [{"source_index": 0, "target_index": 0, "relation_type": "related_to"}],
            candidate_count=2,
        )
        self.assertEqual(result, [])

    def test_out_of_range_index_dropped(self):
        result = validate_intra_source_relations(
            [{"source_index": 0, "target_index": 5, "relation_type": "related_to"}],
            candidate_count=2,
        )
        self.assertEqual(result, [])

    def test_negative_index_dropped(self):
        result = validate_intra_source_relations(
            [{"source_index": -1, "target_index": 0, "relation_type": "related_to"}],
            candidate_count=2,
        )
        self.assertEqual(result, [])

    def test_boolean_index_dropped(self):
        result = validate_intra_source_relations(
            [{"source_index": True, "target_index": False, "relation_type": "related_to"}],
            candidate_count=2,
        )
        self.assertEqual(result, [])

    def test_unsupported_relation_type_dropped(self):
        result = validate_intra_source_relations(
            [{"source_index": 0, "target_index": 1, "relation_type": "not_a_real_type"}],
            candidate_count=2,
        )
        self.assertEqual(result, [])

    def test_duplicate_entry_deduped(self):
        entries = [
            {"source_index": 0, "target_index": 1, "relation_type": "derived_from"},
            {"source_index": 0, "target_index": 1, "relation_type": "derived_from"},
        ]
        result = validate_intra_source_relations(entries, candidate_count=2)
        self.assertEqual(len(result), 1)

    def test_zero_candidate_count_skips_range_check(self):
        # candidate_count=0 means no candidates known — skip range validation
        result = validate_intra_source_relations(
            [{"source_index": 0, "target_index": 1, "relation_type": "related_to"}],
            candidate_count=0,
        )
        self.assertEqual(len(result), 1)

    def test_non_dict_entry_skipped(self):
        result = validate_intra_source_relations(["not a dict", None, 42], candidate_count=3)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Unit tests: parse_intra_source_relations
# ---------------------------------------------------------------------------

class ParseIntraSourceRelationsTest(unittest.TestCase):
    def test_parses_bundle_with_relations(self):
        bundle = json.dumps({
            "schema_version": "mnemos-extraction-v1",
            "candidates": [
                {"title": "Source Note", "type": "source"},
                {"title": "Derived Concept", "type": "concept"},
            ],
            "intra_source_relations": [
                {"source_index": 1, "target_index": 0, "relation_type": "derived_from"},
            ],
        })
        result = parse_intra_source_relations(bundle)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_index"], 1)
        self.assertEqual(result[0]["target_index"], 0)
        self.assertEqual(result[0]["relation_type"], "derived_from")

    def test_invalid_json_returns_empty(self):
        result = parse_intra_source_relations("not json at all")
        self.assertEqual(result, [])

    def test_missing_intra_source_relations_key_returns_empty(self):
        bundle = json.dumps({
            "schema_version": "mnemos-extraction-v1",
            "candidates": [{"title": "A"}, {"title": "B"}],
        })
        result = parse_intra_source_relations(bundle)
        self.assertEqual(result, [])

    def test_out_of_range_index_dropped(self):
        bundle = json.dumps({
            "schema_version": "mnemos-extraction-v1",
            "candidates": [{"title": "A"}, {"title": "B"}],
            "intra_source_relations": [
                {"source_index": 0, "target_index": 99, "relation_type": "related_to"},
            ],
        })
        result = parse_intra_source_relations(bundle)
        self.assertEqual(result, [])

    def test_strips_code_fence(self):
        bundle = "```json\n" + json.dumps({
            "schema_version": "mnemos-extraction-v1",
            "candidates": [{"title": "A"}, {"title": "B"}],
            "intra_source_relations": [
                {"source_index": 0, "target_index": 1, "relation_type": "related_to"},
            ],
        }) + "\n```"
        result = parse_intra_source_relations(bundle)
        self.assertEqual(len(result), 1)


# ---------------------------------------------------------------------------
# Unit tests: FixtureLLMProvider emits intra_source_relations
# ---------------------------------------------------------------------------

class FixtureLLMProviderRelationsTest(unittest.TestCase):
    def test_two_candidates_emit_relation(self):
        provider = FixtureLLMProvider()
        packet = {
            "active_source_ref": "archive:test-001",
            "text_windows": [
                {"window_id": "win-1", "text": "Something meaningful here.\nAnother line."}
            ],
        }
        raw = provider.extract_candidates(packet)
        parsed = json.loads(raw)
        self.assertIn("intra_source_relations", parsed)
        relations = parsed["intra_source_relations"]
        self.assertIsInstance(relations, list)
        self.assertGreater(len(relations), 0)
        rel = relations[0]
        self.assertIn("source_index", rel)
        self.assertIn("target_index", rel)
        self.assertNotIn("source_title", rel)
        self.assertNotIn("target_title", rel)
        self.assertEqual(rel["relation_type"], "derived_from")
        # source_index and target_index must be valid positions in candidates
        candidate_count = len(parsed["candidates"])
        self.assertGreaterEqual(rel["source_index"], 0)
        self.assertLess(rel["source_index"], candidate_count)
        self.assertGreaterEqual(rel["target_index"], 0)
        self.assertLess(rel["target_index"], candidate_count)
        self.assertNotEqual(rel["source_index"], rel["target_index"])

    def test_single_candidate_no_relation(self):
        provider = FixtureLLMProvider()
        # A packet with empty text produces only 1 candidate
        packet = {
            "active_source_ref": "archive:test-001",
            "text_windows": [],
        }
        raw = provider.extract_candidates(packet)
        parsed = json.loads(raw)
        relations = parsed.get("intra_source_relations", [])
        self.assertEqual(relations, [])


# ---------------------------------------------------------------------------
# Unit tests: mark_finished stores intra_source_relations_json
# ---------------------------------------------------------------------------

class MarkFinishedRelationsStorageTest(unittest.TestCase):
    def test_relations_stored_in_queue_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            conn = connect_db(config.paths.db_path)
            run_migrations(conn, ROOT / "migrations")

            item = enqueue_extraction(
                config,
                source_kind="artifact",
                archive_id="test-archive-001",
            )
            with conn:
                mark_processing(conn, item.id)
            relations = [
                {"source_index": 1, "target_index": 0, "relation_type": "derived_from"},
            ]
            with conn:
                mark_finished(
                    conn,
                    queue_id=item.id,
                    status="processed",
                    duplicate_state=None,
                    projection_metadata={},
                    window_count=1,
                    artifact_count=0,
                    visual_snippet_count=0,
                    candidate_count=2,
                    validation_error_count=0,
                    intra_source_relations=relations,
                )
            row = conn.execute(
                "SELECT intra_source_relations_json FROM extraction_queue WHERE id = ?",
                (item.id,),
            ).fetchone()
            stored = json.loads(str(row["intra_source_relations_json"]))
            self.assertEqual(stored, relations)
            conn.close()

    def test_no_relations_stores_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            conn = connect_db(config.paths.db_path)
            run_migrations(conn, ROOT / "migrations")

            item = enqueue_extraction(
                config,
                source_kind="artifact",
                archive_id="test-archive-002",
            )
            with conn:
                mark_processing(conn, item.id)
            with conn:
                mark_finished(
                    conn,
                    queue_id=item.id,
                    status="processed",
                    duplicate_state=None,
                    projection_metadata={},
                    window_count=0,
                    artifact_count=0,
                    visual_snippet_count=0,
                    candidate_count=0,
                    validation_error_count=0,
                )
            row = conn.execute(
                "SELECT intra_source_relations_json FROM extraction_queue WHERE id = ?",
                (item.id,),
            ).fetchone()
            stored = json.loads(str(row["intra_source_relations_json"]))
            self.assertEqual(stored, [])
            conn.close()


# ---------------------------------------------------------------------------
# Unit tests: apply_relation_to_note
# ---------------------------------------------------------------------------

class ApplyRelationToNoteTest(unittest.TestCase):
    def _make_note(self, path: Path, extra_relations: list | None = None) -> None:
        """Write a minimal wiki note with optional existing relations."""
        import yaml
        metadata: dict = {
            "id": "concept/test-note",
            "type": "concept",
            "title": "Test Note",
            "summary": "A test note.",
        }
        if extra_relations:
            metadata["relations"] = extra_relations
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False, default_flow_style=False)
        path.write_text(f"---\n{fm}---\n\nBody text.\n", encoding="utf-8")

    def test_adds_relation(self):
        with tempfile.TemporaryDirectory() as tmp:
            note_path = Path(tmp) / "test-note.md"
            self._make_note(note_path)
            result = apply_relation_to_note(
                note_path, target_note_id="concept/other", relation_type="related_to"
            )
            self.assertTrue(result)
            parsed = split_frontmatter(note_path.read_text())
            relations = parsed.metadata.get("relations", [])
            self.assertEqual(len(relations), 1)
            self.assertEqual(relations[0]["target"], "concept/other")
            self.assertEqual(relations[0]["type"], "related_to")

    def test_idempotent_second_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            note_path = Path(tmp) / "test-note.md"
            self._make_note(note_path, extra_relations=[{"target": "concept/other", "type": "related_to"}])
            result = apply_relation_to_note(
                note_path, target_note_id="concept/other", relation_type="related_to"
            )
            self.assertFalse(result)
            parsed = split_frontmatter(note_path.read_text())
            relations = parsed.metadata.get("relations", [])
            self.assertEqual(len(relations), 1)  # not doubled

    def test_missing_file_returns_false(self):
        result = apply_relation_to_note(
            Path("/tmp/does-not-exist.md"), target_note_id="concept/x", relation_type="related_to"
        )
        self.assertFalse(result)

    def test_appends_to_existing_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            note_path = Path(tmp) / "test-note.md"
            self._make_note(note_path, extra_relations=[{"target": "concept/first", "type": "depends_on"}])
            apply_relation_to_note(note_path, target_note_id="concept/second", relation_type="related_to")
            parsed = split_frontmatter(note_path.read_text())
            relations = parsed.metadata.get("relations", [])
            self.assertEqual(len(relations), 2)


# ---------------------------------------------------------------------------
# Integration test: end-to-end intra-source relation application
# ---------------------------------------------------------------------------

class IntraSourceRelationEndToEndTest(unittest.TestCase):
    """Seed a queue row with intra_source_relations_json + two promoted candidates,
    run process_promotion_queue, verify relations written to frontmatter and
    relations_applied > 0 in result."""

    def _write_note(self, vault_root: Path, note_type: str, slug: str, title: str) -> Path:
        import yaml
        from second_brain.ingest import TYPE_TO_DIRECTORY
        subdir = TYPE_TO_DIRECTORY.get(note_type, note_type + "s")
        note_dir = vault_root / "wiki" / subdir
        note_dir.mkdir(parents=True, exist_ok=True)
        path = note_dir / f"{slug}.md"
        metadata = {
            "id": f"{note_type}/{slug}",
            "type": note_type,
            "title": title,
            "summary": f"Summary of {title}.",
            "lifecycle_state": "draft",
            "corroboration_count": 0,
        }
        fm = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False, default_flow_style=False)
        path.write_text(f"---\n{fm}---\n\n{title} body.\n", encoding="utf-8")
        return path

    def test_relations_applied_after_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = _config(workspace)
            vault_root = config.paths.vault_root
            conn = connect_db(config.paths.db_path)
            run_migrations(conn, ROOT / "migrations")

            # Seed a queue row
            item = enqueue_extraction(config, source_kind="artifact", archive_id="e2e-archive-001")
            with conn:
                mark_processing(conn, item.id)

            # Pre-write the two wiki notes so promotion writer can write them
            # (in a real run, promotion writes them; here we seed extraction_candidates
            # directly and pre-create the note files so the second pass can find them)
            from second_brain.review import slugify
            title_a = "Alpha Source"
            title_b = "Beta Concept"
            slug_a = slugify(title_a)
            slug_b = slugify(title_b)

            # Seed extraction_candidates directly
            candidate_a_id = conn.execute(
                """
                INSERT INTO extraction_candidates
                    (queue_id, candidate_index, candidate_type, title, payload_json,
                     model_provider, model_name, promotion_state)
                VALUES (?, 0, 'source', ?, ?, 'fixture', 'fixture-llm', 'promotion_ready')
                """,
                (item.id, title_a, json.dumps(_minimal_payload(title_a))),
            ).lastrowid
            candidate_b_id = conn.execute(
                """
                INSERT INTO extraction_candidates
                    (queue_id, candidate_index, candidate_type, title, payload_json,
                     model_provider, model_name, promotion_state)
                VALUES (?, 1, 'concept', ?, ?, 'fixture', 'fixture-llm', 'promotion_ready')
                """,
                (item.id, title_b, json.dumps(_minimal_payload(title_b))),
            ).lastrowid
            conn.commit()

            # Store intra_source_relations_json on the queue row
            # candidate_index 0 = Alpha (title_a), candidate_index 1 = Beta (title_b)
            # Relation: Beta (index 1) → derived_from → Alpha (index 0)
            relations = [
                {
                    "source_index": 1,
                    "target_index": 0,
                    "relation_type": "derived_from",
                    "rationale": "Beta derived from Alpha.",
                }
            ]
            with conn:
                mark_finished(
                    conn,
                    queue_id=item.id,
                    status="processed",
                    duplicate_state=None,
                    projection_metadata={},
                    window_count=1,
                    artifact_count=0,
                    visual_snippet_count=0,
                    candidate_count=2,
                    validation_error_count=0,
                    intra_source_relations=relations,
                )
            conn.close()

            # Run promotion — this promotes both candidates and applies relations
            result = process_promotion_queue(config)

            # Both candidates should have been promoted
            self.assertEqual(result.promoted, 2, f"expected 2 promoted, got {result.promoted}; errors={result.errors}")
            self.assertGreaterEqual(result.relations_applied, 1, "expected ≥1 relation applied")

            # The Beta Concept note should have a relation pointing to Alpha Source
            # Both payloads use type 'concept', so note IDs are concept/<slug>
            from second_brain.ingest import TYPE_TO_DIRECTORY
            concepts_dir = vault_root / "wiki" / TYPE_TO_DIRECTORY.get("concept", "concepts")
            beta_path = concepts_dir / f"{slug_b}.md"
            self.assertTrue(beta_path.exists(), f"expected {beta_path} to exist")
            parsed = split_frontmatter(beta_path.read_text())
            rels = parsed.metadata.get("relations", [])
            targets = [r.get("target") for r in rels if isinstance(r, dict)]
            alpha_note_id = f"concept/{slug_a}"
            self.assertIn(alpha_note_id, targets, f"expected {alpha_note_id} in relations; got {targets}")

    def test_fixture_extraction_promotes_relations_to_wiki_and_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = _config(workspace)

            artifact_path = workspace / "alpha.md"
            _write(
                artifact_path,
                """
                # Alpha Source

                Alpha Source introduces the base observation.
                Beta Concept is derived from Alpha Source and refines that observation.
                """,
            )
            ingest = ingest_artifact(config, path=str(artifact_path))
            item = enqueue_extraction(config, source_kind="artifact", archive_id=ingest.archive_id)

            extraction_result = process_extraction_queue(config)
            self.assertEqual(extraction_result.processed, 1)
            self.assertEqual(extraction_result.promotion_ready, 2)

            promotion_result = process_promotion_queue(config)
            self.assertEqual(promotion_result.promoted, 2, promotion_result.errors)
            self.assertEqual(promotion_result.relations_applied, 1)

            source_note_id = f"source/{slugify('Alpha Source')}"
            concept_note_id = f"concept/{slugify('Alpha Source Concept')}"
            concept_path = config.paths.vault_root / "wiki" / "concepts" / f"{slugify('Alpha Source Concept')}.md"
            self.assertTrue(concept_path.exists())
            parsed = split_frontmatter(concept_path.read_text())
            relations = parsed.metadata.get("relations", [])
            self.assertIn(
                {"target": source_note_id, "type": "derived_from"},
                relations,
            )

            conn = connect_db(config.paths.db_path)
            try:
                queue_row = conn.execute(
                    "SELECT intra_source_relations_json FROM extraction_queue WHERE id = ?",
                    (item.id,),
                ).fetchone()
                stored_relations = json.loads(str(queue_row["intra_source_relations_json"]))
                self.assertGreater(len(stored_relations), 0)
                rel = stored_relations[0]
                # Index-based format: source_index/target_index, not titles
                self.assertIn("source_index", rel)
                self.assertIn("target_index", rel)
                self.assertNotIn("source_title", rel)
                self.assertNotIn("target_title", rel)
                self.assertEqual(rel["relation_type"], "derived_from")

                edge = conn.execute(
                    """
                    SELECT source_note_id, target_id, relation_type
                    FROM graph_edges
                    WHERE source_note_id = ? AND target_id = ?
                    """,
                    (concept_note_id, source_note_id),
                ).fetchone()
                self.assertIsNotNone(edge)
                self.assertEqual(edge["relation_type"], "derived_from")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

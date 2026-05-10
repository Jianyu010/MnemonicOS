from __future__ import annotations

from pathlib import Path
import json
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
from second_brain.extraction import enqueue_extraction, extraction_status, process_extraction_queue
from second_brain.extraction.providers import FixtureVLMProvider
from second_brain.extraction.validation import parse_candidate_bundle, validate_candidate
from second_brain.ingest import ingest_artifact, ingest_session
from second_brain.models import AppendEventRequest, BeginEpisodeRequest, EndEpisodeRequest
from second_brain.projection.pipeline_models import ArtifactView
from second_brain.server import _POST_ROUTES
from second_brain.session_capture import append_event, begin_episode, end_episode


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _config(workspace: Path) -> object:
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
    config = load_config(config_path, workspace)
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, ROOT / "migrations")
    connection.close()
    return config


class ExtractionLayerTest(unittest.TestCase):
    def test_migration_creates_extraction_tables_and_drops_legacy_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _config(Path(tmp_dir))
            connection = connect_db(config.paths.db_path)
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            connection.close()
            self.assertIn("extraction_queue", tables)
            self.assertIn("extraction_candidates", tables)
            self.assertIn("extraction_visual_snippets", tables)
            self.assertNotIn("promotion_queue", tables)

    def test_queue_requires_source_identity_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            artifact_path = workspace / "source.md"
            _write(artifact_path, "# Queue Identity\n\nA stable source for extraction.")
            ingest = ingest_artifact(config, path=str(artifact_path))

            first = enqueue_extraction(config, source_kind="artifact", archive_id=ingest.archive_id)
            second = enqueue_extraction(config, source_kind="artifact", archive_id=ingest.archive_id)
            self.assertEqual(first.id, second.id)
            with self.assertRaises(ValueError):
                enqueue_extraction(config, source_kind="artifact")
            with self.assertRaises(ValueError):
                enqueue_extraction(config, source_kind="episode", archive_id=ingest.archive_id)

    def test_dry_run_projection_reports_counts_without_mutating_queue_or_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            artifact_path = workspace / "paper.md"
            _write(artifact_path, "# Projection Dry Run\n\nDry-run extraction should only inspect RAM projection.")
            ingest = ingest_artifact(config, path=str(artifact_path))
            item = enqueue_extraction(config, source_kind="artifact", archive_id=ingest.archive_id)

            result = process_extraction_queue(config, dry_run=True)
            self.assertEqual(result.processed, 1)
            self.assertGreater(result.windows, 0)
            status = extraction_status(config, queue_id=item.id)["item"]
            self.assertEqual(status["status"], "pending")
            self.assertFalse((workspace / "brain/wiki").exists())

    def test_live_fixture_extraction_creates_promotion_ready_candidates_without_wiki_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            artifact_path = workspace / "concept.md"
            _write(artifact_path, "# Local Projection Contract\n\nProjectionLease feeds extraction candidates.")
            ingest = ingest_artifact(config, path=str(artifact_path))
            item = enqueue_extraction(config, source_kind="artifact", archive_id=ingest.archive_id)

            result = process_extraction_queue(config, dry_run=False)
            self.assertEqual(result.processed, 1)
            self.assertGreaterEqual(result.candidates, 1)
            self.assertGreaterEqual(result.promotion_ready, 1)

            connection = connect_db(config.paths.db_path)
            row = connection.execute(
                """
                SELECT validation_status, promotion_state, payload_json
                FROM extraction_candidates
                WHERE queue_id = ?
                ORDER BY candidate_index
                LIMIT 1
                """,
                (item.id,),
            ).fetchone()
            queue = connection.execute("SELECT status, candidate_count FROM extraction_queue WHERE id = ?", (item.id,)).fetchone()
            connection.close()

            self.assertEqual(queue["status"], "processed")
            self.assertGreater(int(queue["candidate_count"]), 0)
            self.assertEqual(row["validation_status"], "promotion_ready")
            self.assertEqual(row["promotion_state"], "promotion_ready")
            payload = json.loads(row["payload_json"])
            self.assertEqual(payload["source_refs"], [ingest.archive_id])
            self.assertTrue(payload["components"])
            self.assertEqual(payload["traces"][0]["component_ids"], payload["temporal_order"])
            self.assertFalse((workspace / "brain/wiki").exists())

    def test_agentic_session_extraction_preserves_decomposed_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            session = ingest_session(
                config,
                bridge_id="codex",
                session_id="calculator-build",
                content=textwrap.dedent(
                    """
                    User request: build a calculator app.
                    Agent tried creating the UI.
                    Agent failed with TypeError in the button handler.
                    Agent searched docs and found event delegation guidance.
                    Agent retried with a delegated click handler.
                    Agent reported success milestone after tests passed.
                    """
                ).strip(),
            )
            item = enqueue_extraction(config, source_kind="session", archive_id=session.archive_id)

            result = process_extraction_queue(config, dry_run=False)
            self.assertEqual(result.processed, 1)

            connection = connect_db(config.paths.db_path)
            row = connection.execute(
                """
                SELECT payload_json
                FROM extraction_candidates
                WHERE queue_id = ? AND validation_status = 'promotion_ready'
                ORDER BY candidate_index
                LIMIT 1
                """,
                (item.id,),
            ).fetchone()
            connection.close()

            payload = json.loads(row["payload_json"])
            component_types = {component["component_type"] for component in payload["components"]}
            self.assertIn("user_request", component_types)
            self.assertIn("failure", component_types)
            self.assertIn("web_finding", component_types)
            self.assertIn("milestone", component_types)
            self.assertEqual(len(payload["temporal_order"]), len(payload["components"]))

    def test_document_extraction_preserves_concepts_and_derivations_as_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            artifact_path = workspace / "chapter.md"
            _write(
                artifact_path,
                """
                # Chapter 1
                Newtonian mechanics is a framework for macroscopic motion.
                The experiment compares predicted and observed trajectories.
                Therefore the text derives conservation guidance from the laws.
                The theorem states momentum remains conserved in the closed system.
                """,
            )
            ingest = ingest_artifact(config, path=str(artifact_path))
            item = enqueue_extraction(config, source_kind="artifact", archive_id=ingest.archive_id)

            process_extraction_queue(config, dry_run=False)

            connection = connect_db(config.paths.db_path)
            row = connection.execute(
                """
                SELECT payload_json
                FROM extraction_candidates
                WHERE queue_id = ? AND validation_status = 'promotion_ready'
                ORDER BY candidate_index
                LIMIT 1
                """,
                (item.id,),
            ).fetchone()
            connection.close()

            payload = json.loads(row["payload_json"])
            component_types = {component["component_type"] for component in payload["components"]}
            self.assertIn("definition", component_types)
            self.assertIn("experiment", component_types)
            self.assertIn("derivation", component_types)
            self.assertIn("theorem", component_types)

    def test_episode_and_session_sources_project_through_same_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            begin = begin_episode(
                config,
                BeginEpisodeRequest(
                    bridge_id="codex",
                    conversation_id="thread",
                    episode_id="episode",
                ),
            )
            append_event(
                config,
                AppendEventRequest(
                    bridge_id="codex",
                    conversation_id=begin.conversation_id,
                    episode_id=begin.episode_id,
                    event_type="user_message",
                    payload={"content": "Episode projection text."},
                ),
            )
            end_episode(
                config,
                EndEpisodeRequest(
                    bridge_id="codex",
                    conversation_id=begin.conversation_id,
                    episode_id=begin.episode_id,
                ),
            )
            session = ingest_session(
                config,
                bridge_id="local",
                session_id="session-source",
                content="Archived session projection text.",
            )
            enqueue_extraction(
                config,
                source_kind="episode",
                conversation_id=begin.conversation_id,
                episode_id=begin.episode_id,
            )
            enqueue_extraction(config, source_kind="session", archive_id=session.archive_id)

            result = process_extraction_queue(config, dry_run=True)
            self.assertEqual(result.processed, 2)
            self.assertGreaterEqual(result.windows, 2)

    def test_visual_fixture_handles_missing_payload_without_crashing(self) -> None:
        artifact = ArtifactView(
            artifact_id="fig-1",
            kind="figure",
            mime_type="image/png",
            payload=None,
            source_attachment_path=None,
            source_locator="page:1",
            marker="[FIGURE:fig-1]",
        )
        snippet = FixtureVLMProvider("fixture-vlm").describe(artifact)
        self.assertIn("visual_payload_missing", snippet.warnings)
        self.assertEqual(snippet.artifact_id, "fig-1")

    def test_candidate_validation_rejects_bad_source_and_bad_schema(self) -> None:
        payloads, errors = parse_candidate_bundle('{"schema_version":"wrong","candidates":[]}')
        self.assertEqual(payloads, [])
        self.assertIn("unsupported_schema_version: wrong", errors)

        candidate = validate_candidate(
            {
                "type": "concept",
                "title": "Wrong source",
                "summary": "summary",
                "body": "body",
                "confidence": "high",
                "source_refs": ["other/source"],
                "evidence": [{"kind": "text_window", "id": "window-1"}],
            },
            active_source_ref="artifact/source",
            evidence_ids={"window-1"},
        )
        self.assertEqual(candidate.validation_status, "invalid")
        self.assertIn("source_refs_missing_active_source", candidate.validation_errors)

    def test_candidate_validation_rejects_summary_only_and_bad_component_structure(self) -> None:
        summary_only = validate_candidate(
            {
                "type": "concept",
                "title": "Summary only",
                "summary": "summary",
                "body": "body",
                "confidence": "high",
                "source_refs": ["artifact/source"],
                "evidence": [{"kind": "text_window", "id": "window-1"}],
            },
            active_source_ref="artifact/source",
            evidence_ids={"window-1"},
        )
        self.assertEqual(summary_only.validation_status, "invalid")
        self.assertIn("missing_required_field: components", summary_only.validation_errors)

        dangling = validate_candidate(
            {
                "type": "concept",
                "title": "Dangling component",
                "summary": "summary",
                "body": "body",
                "confidence": "high",
                "source_refs": ["artifact/source"],
                "evidence": [{"kind": "text_window", "id": "window-1"}],
                "components": [
                    {
                        "component_id": "c1",
                        "component_type": "claim",
                        "text": "A grounded claim.",
                        "evidence": [{"kind": "text_window", "id": "window-1"}],
                        "links": [{"type": "supports", "target": "missing"}],
                    }
                ],
                "temporal_order": ["c1", "missing"],
                "traces": [{"trace_id": "t1", "component_ids": ["c1", "missing"]}],
            },
            active_source_ref="artifact/source",
            evidence_ids={"window-1"},
        )
        self.assertEqual(dangling.validation_status, "invalid")
        self.assertIn("component_link_dangling: c1->missing", dangling.validation_errors)
        self.assertIn("temporal_order_dangling: missing", dangling.validation_errors)
        self.assertIn("trace_component_dangling: t1->missing", dangling.validation_errors)

    def test_server_routes_include_extraction_surfaces(self) -> None:
        self.assertIn("/v1/extraction/queue", _POST_ROUTES)
        self.assertIn("/v1/extraction/process", _POST_ROUTES)


if __name__ == "__main__":
    unittest.main()

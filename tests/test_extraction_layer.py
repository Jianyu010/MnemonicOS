from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.extraction import enqueue_extraction, extraction_status, process_extraction_queue
from second_brain.extraction.queue import pending_items
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

    def test_pending_items_prefers_lightweight_sources_over_heavy_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            connection = connect_db(config.paths.db_path)
            try:
                with connection:
                    for archive_id, source_format in [
                        ("artifact/old-pdf", "pdf"),
                        ("artifact/unknown-artifact", "unknown"),
                        ("artifact/new-markdown", "md"),
                        ("artifact/new-text", "plain_text"),
                        ("artifact/old-docx", "docx"),
                    ]:
                        connection.execute(
                            """
                            INSERT INTO archive(id, type, path, ingested_at, tags, source_format)
                            VALUES (?, 'doc', ?, '2026-05-20 08:00:00', '[]', ?)
                            """,
                            (archive_id, str(workspace / f"{archive_id.replace('/', '-')}.raw"), source_format),
                        )
                    connection.execute(
                        """
                        INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at)
                        VALUES ('artifact', 'artifact/old-pdf', 'pending', '2026-05-20 08:00:00')
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at)
                        VALUES ('artifact', 'artifact/unknown-artifact', 'pending', '2026-05-20 08:04:00')
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at)
                        VALUES ('artifact', 'artifact/new-markdown', 'pending', '2026-05-20 08:05:00')
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at)
                        VALUES ('artifact', 'artifact/new-text', 'pending', '2026-05-20 08:05:30')
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at)
                        VALUES ('session', 'session/new-session', 'pending', '2026-05-20 08:06:00')
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO extraction_queue(
                            source_kind, conversation_id, episode_id, status, queued_at
                        )
                        VALUES ('episode', 'conversation', 'episode', 'pending', '2026-05-20 08:07:00')
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at)
                        VALUES ('artifact', 'artifact/old-docx', 'pending', '2026-05-20 08:10:00')
                        """
                    )

                items = pending_items(connection, limit=10)

                self.assertEqual(
                    [
                        (item.source_kind, item.archive_id, item.conversation_id, item.episode_id)
                        for item in items
                    ],
                    [
                        ("session", "session/new-session", None, None),
                        ("episode", None, "conversation", "episode"),
                        ("artifact", "artifact/new-markdown", None, None),
                        ("artifact", "artifact/new-text", None, None),
                        ("artifact", "artifact/unknown-artifact", None, None),
                        ("artifact", "artifact/old-pdf", None, None),
                        ("artifact", "artifact/old-docx", None, None),
                    ],
                )
            finally:
                connection.close()

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

    def test_pending_quick_items_returns_only_lanes_0_and_1(self) -> None:
        from second_brain.extraction.queue import pending_quick_items, pending_slow_items
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            connection = connect_db(config.paths.db_path)
            try:
                with connection:
                    for archive_id, source_format in [
                        ("artifact/a-pdf", "pdf"),
                        ("artifact/a-md", "md"),
                    ]:
                        connection.execute(
                            "INSERT INTO archive(id, type, path, ingested_at, tags, source_format) "
                            "VALUES (?, 'doc', ?, '2026-05-20 08:00:00', '[]', ?)",
                            (archive_id, str(workspace / f"{archive_id.replace('/', '-')}.raw"), source_format),
                        )
                    connection.execute(
                        "INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at) "
                        "VALUES ('artifact', 'artifact/a-pdf', 'pending', '2026-05-20 08:00:00')"
                    )
                    connection.execute(
                        "INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at) "
                        "VALUES ('artifact', 'artifact/a-md', 'pending', '2026-05-20 08:01:00')"
                    )
                    connection.execute(
                        "INSERT INTO extraction_queue(source_kind, conversation_id, episode_id, status, queued_at) "
                        "VALUES ('episode', 'conv', 'ep1', 'pending', '2026-05-20 08:02:00')"
                    )

                quick = pending_quick_items(connection, limit=10)
                slow = pending_slow_items(connection, limit=10)

                quick_kinds = [(i.source_kind, i.archive_id, i.episode_id) for i in quick]
                slow_kinds = [(i.source_kind, i.archive_id, i.episode_id) for i in slow]

                self.assertEqual(quick_kinds, [
                    ("episode", None, "ep1"),
                    ("artifact", "artifact/a-md", None),
                ])
                self.assertEqual(slow_kinds, [
                    ("artifact", "artifact/a-pdf", None),
                ])
            finally:
                connection.close()

    def test_lane_handle_join_returns_false_on_success(self) -> None:
        import subprocess
        import threading
        from second_brain.extraction.worker import _LaneHandle

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_lane_handle_ok.json"
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.05)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        handle = _LaneHandle(
            proc=proc,
            poll_thread=None,
            stop_poll=threading.Event(),
            progress_file=progress_file,
            timeout_seconds=10,
            stall_timeout_seconds=30,
            process_registry=None,
        )
        timed_out = handle.join()
        self.assertFalse(timed_out)
        self.assertIsNotNone(proc.poll())

    def test_lane_handle_join_returns_true_on_timeout(self) -> None:
        import subprocess
        import threading
        from second_brain.extraction.worker import _LaneHandle

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_lane_handle_timeout.json"
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        handle = _LaneHandle(
            proc=proc,
            poll_thread=None,
            stop_poll=threading.Event(),
            progress_file=progress_file,
            timeout_seconds=1,
            stall_timeout_seconds=30,
            process_registry=None,
        )
        timed_out = handle.join()
        self.assertTrue(timed_out)
        self.assertIsNotNone(proc.poll())

    def test_lane_handle_timeout_reaps_process_group(self) -> None:
        import os
        import signal
        import subprocess
        import threading
        import time
        from second_brain.extraction.worker import _LaneHandle

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_lane_handle_reap.json"
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        handle = _LaneHandle(
            proc=proc,
            poll_thread=None,
            stop_poll=threading.Event(),
            progress_file=progress_file,
            timeout_seconds=1,
            stall_timeout_seconds=30,
            process_registry=None,
        )

        timed_out = handle.join()

        self.assertTrue(timed_out)
        deadline = time.monotonic() + 3.0
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(proc.poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)

    def test_lane_handle_timeout_terminates_before_killing_process_group(self) -> None:
        import signal
        import subprocess
        import threading
        from second_brain.extraction import worker
        from second_brain.extraction.worker import _LaneHandle

        class SlowProc:
            pid = 424242
            returncode = None
            stdout = None
            stderr = None

            def __init__(self) -> None:
                self.calls = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(cmd="slow", timeout=timeout)
                self.returncode = -signal.SIGKILL
                return self.returncode

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_lane_handle_escalate.json"
        proc = SlowProc()
        handle = _LaneHandle(
            proc=proc,  # type: ignore[arg-type]
            poll_thread=None,
            stop_poll=threading.Event(),
            progress_file=progress_file,
            timeout_seconds=1,
            stall_timeout_seconds=30,
            process_registry=None,
        )

        with mock.patch.object(worker.os, "killpg") as killpg:
            timed_out = handle.join()

        self.assertTrue(timed_out)
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(proc.pid, signal.SIGTERM),
                mock.call(proc.pid, signal.SIGKILL),
            ],
        )

    def test_mark_processing_is_atomic_claim(self) -> None:
        """mark_processing must only transition pending->processing once.

        Regression for double-spawn (debug-ingestion B3/B5): the second claim on
        a row that is already 'processing' must return False and must NOT bump
        attempt_count, so a later tick cannot re-dispatch a live item.
        """
        from second_brain.extraction.queue import mark_processing
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            connection = connect_db(config.paths.db_path)
            try:
                with connection:
                    connection.execute(
                        "INSERT INTO extraction_queue(id, source_kind, archive_id, status, queued_at) "
                        "VALUES (1, 'artifact', 'artifact/a', 'pending', '2026-05-20 08:00:00')"
                    )

                with connection:
                    first = mark_processing(connection, 1)
                with connection:
                    second = mark_processing(connection, 1)

                self.assertTrue(first)
                self.assertFalse(second)
                attempt = connection.execute(
                    "SELECT attempt_count FROM extraction_queue WHERE id = 1"
                ).fetchone()[0]
                self.assertEqual(attempt, 1)

                # Once requeued to pending it can be claimed again (resume path).
                with connection:
                    connection.execute(
                        "UPDATE extraction_queue SET status='pending' WHERE id=1"
                    )
                with connection:
                    third = mark_processing(connection, 1)
                self.assertTrue(third)
                attempt = connection.execute(
                    "SELECT attempt_count FROM extraction_queue WHERE id = 1"
                ).fetchone()[0]
                self.assertEqual(attempt, 2)
            finally:
                connection.close()

    def test_lane_handle_join_reaps_when_watch_loop_raises(self) -> None:
        """join() must never return while the subprocess is alive.

        Regression for double-spawn: if the monitoring loop raises, the finally
        block must still kill+reap the subprocess. Otherwise a live orphan is
        left behind, the job lock is released, and a later tick re-dispatches the
        same item (second subprocess, attempt_count bumped).
        """
        import subprocess
        import threading
        import time
        from second_brain.extraction import worker
        from second_brain.extraction.worker import _LaneHandle

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_lane_handle_raise.json"
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        handle = _LaneHandle(
            proc=proc,
            poll_thread=None,
            stop_poll=threading.Event(),
            progress_file=progress_file,
            timeout_seconds=30,
            stall_timeout_seconds=30,
            process_registry=None,
        )

        with mock.patch.object(worker.time, "sleep", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                handle.join()

        deadline = time.monotonic() + 3.0
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(proc.poll())

    def test_lane_handle_timeout_reaps_despite_stderr_flood(self) -> None:
        """Timeout kill must reap even when stderr is flooded and SIGTERM ignored.

        Regression for the pipe-race reaping bug: the old _terminate_process_group
        called proc.communicate() while join()'s drain thread read the same stderr
        pipe. With a child that ignores SIGTERM (forcing the SIGKILL escalation)
        and floods stderr, the concurrent read could make communicate() raise and
        skip SIGKILL, orphaning the subprocess. Reaping must still succeed.
        """
        import os
        import signal
        import subprocess
        import threading
        import time
        from second_brain.extraction.worker import _LaneHandle

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_lane_handle_flood.json"
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass

        child = (
            "import signal,sys,time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "sys.stderr.write('x'*200000)\n"
            "sys.stderr.flush()\n"
            "time.sleep(60)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        handle = _LaneHandle(
            proc=proc,
            poll_thread=None,
            stop_poll=threading.Event(),
            progress_file=progress_file,
            timeout_seconds=1,
            stall_timeout_seconds=30,
            process_registry=None,
        )

        timed_out = handle.join()

        self.assertTrue(timed_out)
        deadline = time.monotonic() + 5.0
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(proc.poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)

    def test_file_progress_flushes_item_assignment(self) -> None:
        from second_brain.extraction._subprocess_entry import _FileProgress

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_file_progress_assignment.json"
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass

        progress = _FileProgress(str(progress_file))
        progress["phase"] = "llm"
        progress["window"] = 2

        payload = json.loads(progress_file.read_text())
        self.assertEqual(payload["phase"], "llm")
        self.assertEqual(payload["window"], 2)

    def test_process_item_reports_vlm_phase_before_describing_artifacts(self) -> None:
        from datetime import datetime, timedelta
        from second_brain.extraction import worker
        from second_brain.extraction.models import ExtractionQueueItem, VisualSnippet
        from second_brain.projection.pipeline_models import (
            ArtifactMarker,
            ProjectionLease,
            SourceRef,
            SourceSpan,
            TextWindow,
        )

        class FakeService:
            def open_projection(self, source_ref, options):
                now = datetime.now()
                return ProjectionLease(
                    lease_id="lease-1",
                    source_ref=source_ref,
                    duplicate_state="new",
                    canonical_source_ref=None,
                    opened_at=now,
                    expires_at=now + timedelta(minutes=5),
                    warnings=[],
                    projection_metadata={},
                )

            def iter_text_windows(self, lease_id):
                return [
                    TextWindow(
                        window_id="w1",
                        text="A source window with enough content to extract.",
                        source_spans=[
                            SourceSpan(
                                source_kind="artifact",
                                source_locator="page 1",
                                char_start=0,
                                char_end=20,
                            )
                        ],
                        artifact_markers=[
                            ArtifactMarker(
                                artifact_id="fig-1",
                                kind="figure",
                                marker_text="[figure]",
                                char_offset=0,
                            )
                        ],
                        token_estimate=10,
                    )
                ]

            def iter_artifacts(self, lease_id):
                return [
                    ArtifactView(
                        artifact_id="fig-1",
                        kind="figure",
                        mime_type="image/png",
                        payload=b"not-real-image",
                        source_attachment_path=None,
                        source_locator="page 1",
                        marker="[figure]",
                    )
                ]

            def close_projection(self, lease_id):
                pass

        class AssertingVLM:
            provider_name = "asserting-vlm"
            model_name = "asserting-vlm"

            def __init__(self, progress):
                self.progress = progress

            def describe(self, artifact):
                self.progress_snapshot = dict(self.progress)
                if self.progress.get("phase") != "vlm":
                    raise AssertionError(f"expected vlm progress, got {self.progress!r}")
                return VisualSnippet(
                    artifact_id=artifact.artifact_id,
                    marker=artifact.marker,
                    source_locator=artifact.source_locator,
                    visual_description="figure description",
                    visible_text="",
                    warnings=[],
                )

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            connection = connect_db(config.paths.db_path)
            try:
                with connection:
                    connection.execute(
                        "INSERT INTO extraction_queue(id, source_kind, archive_id, status) "
                        "VALUES (42, 'artifact', 'artifact/progress-test', 'processing')"
                    )
                item = ExtractionQueueItem(
                    id=42,
                    source_kind="artifact",
                    archive_id="artifact/progress-test",
                    conversation_id=None,
                    episode_id=None,
                    bridge_id="test",
                    session_id=None,
                    task_id=None,
                    duplicate_state=None,
                    status="processing",
                    attempt_count=1,
                    last_error=None,
                )
                progress: dict[str, object] = {}
                with mock.patch.object(worker, "build_vlm_provider", return_value=AssertingVLM(progress)):
                    worker._process_item(
                        config,
                        FakeService(),
                        connection,
                        item=item,
                        dry_run=False,
                        progress=progress,
                    )
                self.assertEqual(progress["phase"], "storing")
            finally:
                connection.close()

    def test_dual_lane_process_respects_per_run_batch_caps(self) -> None:
        from second_brain.extraction import worker

        class FakeHandle:
            def join(self) -> bool:
                return False

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            config.extraction.dual_lane_enabled = True
            config.extraction.quick_lane_batch = 2
            config.extraction.slow_lane_batch = 1

            connection = connect_db(config.paths.db_path)
            try:
                with connection:
                    for index in range(2):
                        archive_id = f"artifact/slow-{index}"
                        connection.execute(
                            "INSERT INTO archive(id, type, path, ingested_at, tags, source_format) "
                            "VALUES (?, 'doc', ?, '2026-05-20 08:00:00', '[]', 'pdf')",
                            (archive_id, str(workspace / f"slow-{index}.pdf")),
                        )
                        connection.execute(
                            "INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at) "
                            "VALUES ('artifact', ?, 'pending', ?)",
                            (archive_id, f"2026-05-20 08:0{index}:00"),
                        )
                    for index in range(3):
                        connection.execute(
                            "INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at) "
                            "VALUES ('session', ?, 'pending', ?)",
                            (f"session/quick-{index}", f"2026-05-20 08:1{index}:00"),
                        )
            finally:
                connection.close()

            def finish_item(conn, stats, queue_id):
                stats.processed += 1
                with conn:
                    conn.execute(
                        "UPDATE extraction_queue SET status = 'processed', finished_at = datetime('now') WHERE id = ?",
                        (queue_id,),
                    )

            with (
                mock.patch.object(worker, "_spawn_item_subprocess", return_value=FakeHandle()) as spawn,
                mock.patch.object(worker, "_accumulate_subprocess_result", side_effect=finish_item),
            ):
                result = process_extraction_queue(config, dry_run=False)

            self.assertEqual(result.processed, 3)
            self.assertEqual(spawn.call_count, 3)

            connection = connect_db(config.paths.db_path)
            try:
                pending_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM extraction_queue WHERE status = 'pending'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(int(pending_count["count"]), 2)


    def test_extraction_config_defaults_stall_timeout(self) -> None:
        from second_brain.config import ExtractionConfig
        cfg = ExtractionConfig()
        self.assertEqual(cfg.stall_timeout_seconds, 900)
        self.assertEqual(cfg.max_extraction_item_seconds, 7200)

    def test_lane_handle_stall_kills_when_progress_file_unchanged(self) -> None:
        import subprocess
        import threading
        from second_brain.extraction.worker import _LaneHandle

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_stall_unchanged.json"
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass

        # Process sleeps forever and never writes the progress file.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        handle = _LaneHandle(
            proc=proc,
            poll_thread=None,
            stop_poll=threading.Event(),
            progress_file=progress_file,
            timeout_seconds=30,       # absolute deadline: won't hit
            stall_timeout_seconds=2,  # stall kill: 2s (hits first)
            process_registry=None,
        )
        timed_out = handle.join()
        self.assertTrue(timed_out)
        self.assertIsNotNone(proc.poll())

    def test_lane_handle_no_kill_when_progress_file_keeps_updating(self) -> None:
        import subprocess
        import threading
        from second_brain.extraction.worker import _LaneHandle

        progress_file = Path(tempfile.gettempdir()) / "mnemo_test_stall_updating.json"
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass

        # Write to progress file every 0.3s for ~1.5s then exit.
        script = "\n".join([
            "import time, pathlib",
            f"p = pathlib.Path({str(progress_file)!r})",
            "for i in range(5):",
            "    p.write_text(str(i))",
            "    time.sleep(0.3)",
        ])
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        handle = _LaneHandle(
            proc=proc,
            poll_thread=None,
            stop_poll=threading.Event(),
            progress_file=progress_file,
            timeout_seconds=30,      # absolute: 30s (won't hit)
            stall_timeout_seconds=2, # stall: 2s (won't hit — file keeps updating)
            process_registry=None,
        )
        timed_out = handle.join()
        self.assertFalse(timed_out)  # exits naturally
        self.assertIsNotNone(proc.poll())
        self.assertEqual(proc.returncode, 0)

    def test_dual_lane_quick_independent_from_slow_timeout(self) -> None:
        """Quick lane items complete even if slow lane times out."""
        from second_brain.extraction import worker

        class QuickHandle:
            def join(self) -> bool:
                return False

        class SlowHandle:
            def join(self) -> bool:
                return True  # simulates a timeout

        def fake_spawn(*, progress_key, **kw):
            return SlowHandle() if progress_key == "slow" else QuickHandle()

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)
            config.extraction.dual_lane_enabled = True
            config.extraction.quick_lane_batch = 2
            config.extraction.slow_lane_batch = 1

            connection = connect_db(config.paths.db_path)
            try:
                with connection:
                    connection.execute(
                        "INSERT INTO archive(id, type, path, ingested_at, tags, source_format) "
                        "VALUES ('artifact/pdf-heavy', 'doc', '/dev/null', '2026-05-20 08:00:00', '[]', 'pdf')"
                    )
                    connection.execute(
                        "INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at) "
                        "VALUES ('artifact', 'artifact/pdf-heavy', 'pending', '2026-05-20 08:00:00')"
                    )
                    for i in range(2):
                        connection.execute(
                            "INSERT INTO extraction_queue(source_kind, archive_id, status, queued_at) "
                            "VALUES ('session', ?, 'pending', ?)",
                            (f"session/quick-{i}", f"2026-05-20 08:0{i + 1}:00"),
                        )
            finally:
                connection.close()

            def accumulate(conn, stats, queue_id):
                stats.processed += 1
                with conn:
                    conn.execute(
                        "UPDATE extraction_queue SET status='processed', finished_at=datetime('now') WHERE id=?",
                        (queue_id,),
                    )

            with (
                mock.patch.object(worker, "_spawn_item_subprocess", side_effect=fake_spawn),
                mock.patch.object(worker, "_accumulate_subprocess_result", side_effect=accumulate),
                mock.patch.object(worker, "mark_error"),
            ):
                result = process_extraction_queue(config, dry_run=False)

        # Both quick items processed; slow item timed out
        self.assertEqual(result.processed, 2)
        self.assertEqual(result.errors, 1)


    def test_inference_timeout_error_raised_on_socket_timeout(self) -> None:
        """_open_request converts socket.timeout into InferenceTimeoutError."""
        import socket as _socket
        from second_brain.extraction.providers import InferenceTimeoutError, _open_request
        from urllib import request as _req

        req = _req.Request("http://127.0.0.1:11434/api/generate", method="GET")
        with mock.patch("second_brain.extraction.providers.request.build_opener") as mock_opener:
            instance = mock_opener.return_value
            instance.open.side_effect = _socket.timeout("timed out")
            with self.assertRaises(InferenceTimeoutError):
                with _open_request(req, timeout_seconds=1):
                    pass

    def test_socket_timeout_not_retried_as_transient(self) -> None:
        """socket.timeout must NOT be in _TRANSIENT_ERRORS (would cause infinite retry)."""
        import socket as _socket
        from second_brain.extraction.providers import _TRANSIENT_ERRORS
        self.assertNotIn(_socket.timeout, _TRANSIENT_ERRORS)
        self.assertNotIn(TimeoutError, _TRANSIENT_ERRORS)

    def test_ollama_vlm_provider_passes_timeout_and_keep_alive(self) -> None:
        """OllamaVLMProvider passes timeout_seconds and keep_alive=0 to _ollama_generate."""
        from second_brain.extraction.providers import OllamaVLMProvider
        from second_brain.projection.pipeline_models import ArtifactView

        artifact = ArtifactView(
            artifact_id="fig-1",
            kind="figure",
            mime_type="image/png",
            payload=b"fake-image",
            source_attachment_path=None,
            source_locator="page:1",
            marker="[FIGURE:fig-1]",
        )
        provider = OllamaVLMProvider(
            "llama3.2-vision:latest",
            base_url="http://127.0.0.1:11434",
            timeout_seconds=42,
            keep_alive=0,
        )
        with mock.patch(
            "second_brain.extraction.providers._ollama_generate", return_value="{}"
        ) as mock_gen:
            provider.describe(artifact)
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        self.assertEqual(kwargs["timeout_seconds"], 42)
        self.assertEqual(kwargs["keep_alive"], 0)

    def test_ollama_llm_provider_passes_timeout(self) -> None:
        """OllamaLLMProvider passes timeout_seconds to _ollama_generate."""
        from second_brain.extraction.providers import OllamaLLMProvider

        provider = OllamaLLMProvider(
            "qwen3:8b",
            base_url="http://127.0.0.1:11434",
            timeout_seconds=99,
        )
        with mock.patch(
            "second_brain.extraction.providers._ollama_generate", return_value="{}"
        ) as mock_gen:
            provider.extract_candidates({"text_windows": [], "active_source_ref": "x/y"})
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        self.assertEqual(kwargs["timeout_seconds"], 99)

    def test_subprocess_entry_inference_timeout_not_tombstoned(self) -> None:
        """InferenceTimeoutError always writes 'timeout:' prefix, never 'tombstoned:'."""
        from second_brain.extraction._subprocess_entry import _run
        from second_brain.extraction.providers import InferenceTimeoutError

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = _config(workspace)

            connection = connect_db(config.paths.db_path)
            try:
                with connection:
                    connection.execute(
                        "INSERT INTO archive(id, type, path, ingested_at, tags) "
                        "VALUES ('artifact/timeout-test', 'doc', '/dev/null', '2026-05-21', '[]')"
                    )
                    connection.execute(
                        "INSERT INTO extraction_queue(id, source_kind, archive_id, status, attempt_count) "
                        "VALUES (77, 'artifact', 'artifact/timeout-test', 'processing', 3)"
                    )
            finally:
                connection.close()

            # Raise InferenceTimeoutError from _process_item — simulates a VLM hang.
            # Patch at the worker module since _process_item is a local import inside _run().
            with mock.patch(
                "second_brain.extraction.worker._process_item",
                side_effect=InferenceTimeoutError("timed out after 240s"),
            ):
                exit_code = _run(77, config_path=str(config.config_path))

            self.assertEqual(exit_code, 1)
            connection = connect_db(config.paths.db_path)
            try:
                row = connection.execute(
                    "SELECT status, last_error FROM extraction_queue WHERE id = 77"
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(row["status"], "error")
            self.assertTrue(
                str(row["last_error"]).startswith("timeout:"),
                f"Expected 'timeout:' prefix, got: {row['last_error']!r}",
            )
            self.assertNotIn("tombstoned", str(row["last_error"]))


if __name__ == "__main__":
    unittest.main()

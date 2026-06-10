from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import ExtractionConfig, load_config
from second_brain.daemon import Daemon
from second_brain.daemon_jobs import _DEFAULT_SCHEDULES, build_registry
from second_brain.daemon_status import _render_watch, daemon_status_summary
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import ingest_session
from second_brain.jobs import _STALE_PROCESSING_TIMEOUT_MINUTES, run_job


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class DaemonJobsTest(unittest.TestCase):
    def _prepare_workspace(self, workspace: Path) -> object:
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
            enabled = true
            provider = "hash"
            model = "hash-128-v1"
            dimensions = 128

            [graph]
            enabled = false

            [ingest]
            auto_trigger_pipeline = false

            [daemon]
            socket_path = "{(workspace / 'brain/data/mnemonic.sock').as_posix()}"
            pid_path = "{(workspace / 'brain/data/mnemonic.pid').as_posix()}"
            log_path = "{(workspace / 'brain/logs/daemon.log').as_posix()}"
            max_pending = 0
            max_oldest_age_minutes = 0

            [daemon.schedules]
            embed_backfill = "15 * * * *"
            status_report = "0 * * * *"
            """,
        )
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def test_intake_archives_without_queue_or_canonical_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            result = ingest_session(
                config,
                bridge_id="automation",
                session_id="raw-only",
                content="task_kind: knowledge\n\n## Task\nArchive this raw session.\n",
            )
            self.assertEqual(result.promoted_notes, [])
            connection = connect_db(config.paths.db_path)
            note_count = connection.execute("SELECT COUNT(*) AS count FROM notes").fetchone()
            vector_count = connection.execute("SELECT COUNT(*) AS count FROM archive_chunk_vectors").fetchone()
            queue_exists = connection.execute(
                "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = 'promotion_queue'"
            ).fetchone()
            connection.close()
            self.assertEqual(int(note_count["count"]), 0)
            self.assertEqual(int(vector_count["count"]), 0)
            self.assertEqual(int(queue_exists["count"]), 0)

    def test_embed_backfill_populates_archive_chunk_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            ingest_session(
                config,
                bridge_id="automation",
                session_id="embed-me",
                content=("This is a long enough archive payload to produce searchable chunks. " * 80).strip() + "\n",
            )
            before = connect_db(config.paths.db_path)
            missing = before.execute("SELECT COUNT(*) AS count FROM archive_chunk_vectors").fetchone()
            before.close()
            self.assertEqual(int(missing["count"]), 0)

            result = run_job(config, job_name="embed_backfill", dry_run=False)
            self.assertGreater(result.stats["archive_chunks_embedded"], 0)

            after = connect_db(config.paths.db_path)
            vector_count = after.execute("SELECT COUNT(*) AS count FROM archive_chunk_vectors").fetchone()
            after.close()
            self.assertGreater(int(vector_count["count"]), 0)

    def test_daemon_control_plane_reports_status_and_runs_remaining_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            daemon = Daemon(config)
            status = daemon.socket_server._handle_request({"cmd": "status"})
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["health"]["queue"]["pending_count"], 0)

            response = daemon.socket_server._handle_request({"cmd": "run-job", "job": "status_report"})
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["result"]["status"], "ok")

            # Simulate a concurrent run by holding the per-job lock.
            job_lock = daemon.scheduler._get_job_lock("status_report")
            job_lock.acquire()
            try:
                busy = daemon.scheduler.run_job_now("status_report")
            finally:
                job_lock.release()
            self.assertEqual(busy["status"], "busy")

    def test_process_registry_terminates_registered_process_group(self) -> None:
        from second_brain.daemon_processes import DaemonProcessRegistry
        import os
        import signal
        import subprocess
        import time

        registry = DaemonProcessRegistry()
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        registry.register(proc, label="test-sleeper", process_group=True)
        try:
            stopped = registry.stop_all(grace_seconds=0.2, kill_seconds=1.0)
            self.assertTrue(stopped)
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=2.0)

    def test_daemon_scheduler_has_process_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            daemon = Daemon(config)
            self.assertTrue(hasattr(daemon.scheduler, "process_registry"))
            self.assertIs(daemon.scheduler.process_registry, daemon.process_registry)

    def test_daemon_socket_shutdown_sets_shutdown_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            daemon = Daemon(config)
            response = daemon.socket_server._handle_request({"cmd": "shutdown"})
            self.assertEqual(response["status"], "ok")
            self.assertTrue(daemon.shutdown_event.is_set())

    def test_daemon_owned_ollama_config_defaults_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            self.assertFalse(config.daemon.ollama_managed)
            self.assertEqual(config.daemon.ollama_command, "")

    def test_daemon_stop_postcondition_requires_socket_removal(self) -> None:
        from second_brain.cli import _daemon_stop_postcondition_met

        with tempfile.TemporaryDirectory() as tmp_dir:
            socket_path = Path(tmp_dir) / "mnemonic.sock"
            socket_path.write_text("", encoding="utf-8")
            self.assertFalse(_daemon_stop_postcondition_met(None, [], socket_path))
            socket_path.unlink()
            self.assertTrue(_daemon_stop_postcondition_met(None, [], socket_path))

    def test_daemon_stop_refreshes_process_group_for_live_pid(self) -> None:
        from second_brain.cli import _refresh_daemon_process_groups
        import os
        import signal
        import subprocess

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.assertIn(proc.pid, _refresh_daemon_process_groups(proc.pid, []))
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=2.0)

    def test_daemon_status_summary_includes_units_and_extraction_schedule(self) -> None:
        response = {
            "status": "ok",
            "service": {"running_job": None, "scheduled_jobs": []},
            "health": {
                "queue": {
                    "pending_count": 3,
                    "error_count": 1,
                    "oldest_pending_age_minutes": 125,
                    "warnings": ["queue depth warning"],
                },
                "storage": {
                    "db_bytes": 1536,
                    "vault_bytes": 4096,
                    "note_vectors_count": 42,
                    "chunk_vectors_count": 17,
                    "graph_nodes_count": 100,
                    "graph_edges_count": 83,
                },
                "recent_job_runs": [
                    {"job_name": "status_report", "status": "ok", "finished_at": "2026-04-17 11:00:00"},
                ],
            },
        }
        text = daemon_status_summary(response)
        self.assertIn("Extraction Queue", text)
        self.assertIn("next extraction run: unknown", text)
        self.assertIn("oldest pending age: 2 hr 5 min", text)
        self.assertIn("db: 1.5 KB", text)
        self.assertIn("vectors: 42 notes · 17 chunks", text)

    def test_daemon_defaults_include_maintenance_jobs_without_legacy_rollups(self) -> None:
        expected = {
            "wiki_embedding_neighbor_backfill",
            "wiki_cluster_validation",
            "wiki_pattern_discovery",
            "wiki_graph_rebuild",
            "wiki_lifecycle_audit",
            "wiki_importance_scoring",
            "wiki_health_report",
            "wiki_raw_revisitation",
        }
        self.assertTrue(expected.issubset(_DEFAULT_SCHEDULES))
        self.assertNotIn("daemon_daily", _DEFAULT_SCHEDULES)
        self.assertNotIn("daemon_weekly", _DEFAULT_SCHEDULES)
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            registry = build_registry(config)
            self.assertTrue(expected.issubset(set(registry.job_names())))


    def test_extraction_config_dual_lane_defaults(self) -> None:
        from second_brain.config import ExtractionConfig
        cfg = ExtractionConfig()
        self.assertFalse(cfg.dual_lane_enabled)
        self.assertEqual(cfg.quick_lane_batch, 5)
        self.assertEqual(cfg.slow_lane_batch, 5)

    def test_extraction_config_dual_lane_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "memory.toml").write_text(textwrap.dedent("""\
                [extraction]
                dual_lane_enabled = true
                quick_lane_batch = 3
                slow_lane_batch = 2
            """))
            cfg = load_config(root)
            self.assertTrue(cfg.extraction.dual_lane_enabled)
            self.assertEqual(cfg.extraction.quick_lane_batch, 3)
            self.assertEqual(cfg.extraction.slow_lane_batch, 2)

    def test_daemon_status_dual_lane_progress_rendering(self) -> None:
        response = {
            "status": "ok",
            "service": {
                "running_job": "process_extraction_queue",
                "running_jobs": ["process_extraction_queue"],
                "scheduled_jobs": [],
                "job_progress": {
                    "mode": "dual_lane",
                    "quick": {
                        "item_id": 1,
                        "item_name": "episode/quick-item",
                        "item_index": 0,
                        "total_items": 5,
                        "phase": "llm",
                        "window": 0,
                        "total_windows": 5,
                    },
                    "slow": {
                        "item_id": 2,
                        "item_name": "artifact/big.pdf",
                        "item_index": 1,
                        "total_items": 4,
                        "phase": "vlm",
                        "window": 0,
                        "total_windows": 5,
                        "artifact": 1,
                        "total_artifacts": 4,
                    },
                },
            },
            "health": {
                "queue": {
                    "pending_count": 4,
                    "error_count": 0,
                    "oldest_pending_age_minutes": 10,
                    "warnings": [],
                },
                "storage": {
                    "db_bytes": 2048,
                    "vault_bytes": 8192,
                    "note_vectors_count": 20,
                    "chunk_vectors_count": 8,
                    "graph_nodes_count": 15,
                    "graph_edges_count": 7,
                },
                "recent_job_runs": [],
            },
        }
        text = daemon_status_summary(response)
        self.assertIn("quick lane", text)
        self.assertIn("slow lane", text)
        self.assertIn("episode/quick-item", text)
        self.assertIn("artifact/big.pdf", text)
        self.assertIn("item 1/5", text)
        self.assertIn("item 2/4", text)
        self.assertIn("window 1/5 active", text)
        self.assertIn("artifact 1/4", text)

    def test_daemon_watch_falls_back_to_processing_pdf_when_slow_progress_is_empty(self) -> None:
        import json
        from datetime import datetime, timezone

        progress_file = Path(tempfile.gettempdir()) / "mnemo_progress_840.json"
        try:
            progress_file.write_text(
                json.dumps({
                    "phase": "vlm",
                    "window": 0,
                    "total_windows": 1,
                    "artifact": 11,
                    "total_artifacts": 50,
                })
            )
            response = {
                "status": "ok",
                "service": {
                    "running_job": "process_extraction_queue",
                    "running_jobs": ["process_extraction_queue"],
                    "job_progress": {
                        "mode": "dual_lane",
                        "quick": {
                            "item_id": 1628,
                            "item_name": "1628",
                            "item_index": 4,
                            "total_items": 10,
                            "phase": "llm",
                            "window": 0,
                            "total_windows": 1,
                        },
                        "slow": {},
                    },
                },
                "health": {
                    "queue": {
                        "queue_counts": {
                            "pending": 271,
                            "processing": 3,
                            "error": 127,
                            "processed": 1371,
                        },
                        "oldest_pending_age_minutes": 8882,
                    },
                    "promotion": {},
                    "recent_job_runs": [],
                },
            }
            processing_items = [
                {
                    "id": 840,
                    "source_kind": "artifact",
                    "archive_id": "artifact/2026-05-14-204616-999788-gasiorowicz-s-quantum-physics",
                    "source_format": "pdf",
                    "original_filename": "Gasiorowicz S. - Quantum Physics.pdf",
                    "started_at": "2026-05-20 15:30:17",
                    "attempt_count": 18,
                    "window_count": 0,
                    "candidate_count": 0,
                },
            ]

            text = _render_watch(response, processing_items, datetime.now(timezone.utc), 3)

            self.assertIn("slow lane", text)
            self.assertIn("Gasiorowicz S. - Quantum Physics.pdf", text)
            self.assertIn("vlm", text)
            self.assertIn("artifact", text)
            self.assertNotIn("slow lane: \x1b[2midle", text)
        finally:
            try:
                progress_file.unlink()
            except FileNotFoundError:
                pass


class RetryExtractionRaceTest(unittest.TestCase):
    """Tests for the double-spawn race between retry_failed_extractions and
    process_extraction_queue.  See brain/system/debug-ingestion.md for full
    incident writeup."""

    def _prepare_workspace(self, workspace: Path) -> object:
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

            [ingest]
            auto_trigger_pipeline = false

            [daemon]
            socket_path = "{(workspace / 'brain/data/mnemonic.sock').as_posix()}"
            pid_path = "{(workspace / 'brain/data/mnemonic.pid').as_posix()}"
            log_path = "{(workspace / 'brain/logs/daemon.log').as_posix()}"
            max_pending = 999
            max_oldest_age_minutes = 999
            """,
        )
        config = load_config(config_path, workspace)
        conn = connect_db(config.paths.db_path)
        run_migrations(conn, ROOT / "migrations")
        conn.close()
        return config

    def _insert_processing_item(
        self,
        conn,
        *,
        started_minutes_ago: float,
        attempt_count: int = 1,
    ) -> int:
        """Insert an episode item in 'processing' state. Returns the row id."""
        started_at = datetime.now(timezone.utc) - timedelta(minutes=started_minutes_ago)
        started_at_str = started_at.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO extraction_queue(
                source_kind, conversation_id, episode_id,
                status, attempt_count,
                queued_at, started_at,
                projection_metadata_json, intra_source_relations_json
            ) VALUES (
                'episode', 'test-conv-id', 'test-ep-id',
                'processing', ?,
                datetime('now'), ?,
                '{}', '[]'
            )
            """,
            (attempt_count, started_at_str),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # ------------------------------------------------------------------ #
    # B5 — configuration invariant                                         #
    # ------------------------------------------------------------------ #

    def test_stale_timeout_exceeds_max_extraction_item_seconds(self) -> None:
        """_STALE_PROCESSING_TIMEOUT_MINUTES must be strictly greater than
        max_extraction_item_seconds / 60.

        If stale_timeout <= max_extraction_seconds, retry_failed_extractions
        will reset items that are still legitimately running, causing the
        double-spawn race (see brain/system/debug-ingestion.md B5).
        """
        max_minutes = ExtractionConfig().max_extraction_item_seconds / 60
        self.assertGreater(
            _STALE_PROCESSING_TIMEOUT_MINUTES,
            max_minutes,
            msg=(
                f"_STALE_PROCESSING_TIMEOUT_MINUTES ({_STALE_PROCESSING_TIMEOUT_MINUTES}m) "
                f"<= max_extraction_item_seconds ({max_minutes}m): "
                "retry_failed_extractions can reset active subprocesses mid-run, "
                "causing a double-spawn (see brain/system/debug-ingestion.md B5)."
            ),
        )

    # ------------------------------------------------------------------ #
    # B5 — retry_failed_extractions stale-reset behaviour                  #
    # ------------------------------------------------------------------ #

    def test_retry_does_not_reset_item_within_extraction_window(self) -> None:
        """An item in 'processing' for less than the stale timeout must NOT
        be reset.  Resetting it while the subprocess is alive causes the
        double-spawn race."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            conn = connect_db(config.paths.db_path)

            # started 30 min ago — well within the stale window
            item_id = self._insert_processing_item(conn, started_minutes_ago=30)
            conn.close()

            run_job(config, job_name="retry_failed_extractions", dry_run=False)

            conn = connect_db(config.paths.db_path)
            row = conn.execute(
                "SELECT status FROM extraction_queue WHERE id = ?", (item_id,)
            ).fetchone()
            conn.close()
            self.assertEqual(
                row["status"],
                "processing",
                "Item started 30 min ago must stay 'processing' — it is within the "
                "extraction window and its subprocess may still be running.",
            )

    def test_retry_resets_genuinely_stale_processing_item(self) -> None:
        """An item in 'processing' for longer than the stale timeout (and with
        attempt_count below the tombstone threshold) must be reset to 'pending'
        so the next extraction job can pick it up."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            conn = connect_db(config.paths.db_path)

            # started well past the stale threshold, low attempt_count → reset not tombstone
            stale_minutes = _STALE_PROCESSING_TIMEOUT_MINUTES + 30
            item_id = self._insert_processing_item(
                conn, started_minutes_ago=stale_minutes, attempt_count=1
            )
            conn.close()

            run_job(config, job_name="retry_failed_extractions", dry_run=False)

            conn = connect_db(config.paths.db_path)
            row = conn.execute(
                "SELECT status FROM extraction_queue WHERE id = ?", (item_id,)
            ).fetchone()
            conn.close()
            self.assertIn(
                row["status"],
                ("pending", "error"),
                "Genuinely stale processing item must be reset to 'pending' or "
                "tombstoned — it should never stay 'processing' after retry runs.",
            )

    def test_retry_does_not_reset_item_at_exact_max_extraction_boundary(self) -> None:
        """An item that has been processing for exactly max_extraction_item_seconds
        (the absolute kill threshold) must not be reset before it would be killed
        by the extraction worker itself.

        This is the core of the double-spawn race: if stale_timeout <=
        max_extraction_seconds, the retry job wins the race and re-queues the item
        while the subprocess is still alive.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            conn = connect_db(config.paths.db_path)

            # started exactly at the max extraction time boundary
            max_minutes = config.extraction.max_extraction_item_seconds / 60
            item_id = self._insert_processing_item(
                conn, started_minutes_ago=max_minutes, attempt_count=1
            )
            conn.close()

            run_job(config, job_name="retry_failed_extractions", dry_run=False)

            conn = connect_db(config.paths.db_path)
            row = conn.execute(
                "SELECT status FROM extraction_queue WHERE id = ?", (item_id,)
            ).fetchone()
            conn.close()
            self.assertEqual(
                row["status"],
                "processing",
                "Item at the max-extraction-time boundary must still be 'processing': "
                "the subprocess is legitimately alive up to this point and resetting "
                "it would cause a double-spawn.",
            )


class ActiveFileRefreshTest(unittest.TestCase):
    """wiki_health_report must refresh brain/system/ACTIVE.md.

    The May-2026 maintenance-layer refactor dropped the refresh_active_file
    call from the job graph, freezing ACTIVE.md — which is pinned into every
    retrieval context — at its last manual update."""

    def test_wiki_health_report_refreshes_active_md(self) -> None:
        from datetime import datetime as _dt

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = DaemonJobsTest._prepare_workspace(self, workspace)  # type: ignore[arg-type]
            active_path = workspace / "brain/system/ACTIVE.md"
            active_path.write_text("# ACTIVE\nupdated_at: 2020-01-01\n")

            result = run_job(config, job_name="wiki_health_report", dry_run=False)
            self.assertEqual(result.status, "ok")

            content = active_path.read_text()
            today = _dt.now().strftime("%Y-%m-%d")
            self.assertIn(f"updated_at: {today}", content)
            self.assertIn("## Needs Human Review", content)
            self.assertIn("## Relearn Queue", content)


class SocketSendTest(unittest.TestCase):
    """socket_send must distinguish 'unreachable' from 'delivered, reply slow'.

    Long-running daemon jobs reply only after completion. If the client treats
    a post-delivery read timeout as 'daemon down' the CLI falls back to running
    the job in-process — a duplicate concurrent run that collides on the DB
    write lock ('database is locked')."""

    def test_returns_none_when_socket_unreachable(self) -> None:
        from second_brain.daemon_status import socket_send

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such.sock"
            self.assertIsNone(socket_send(missing, {"cmd": "status"}))

    def test_returns_timeout_status_when_reply_exceeds_timeout(self) -> None:
        import socket as socketlib
        import threading

        from second_brain.daemon_status import socket_send

        with tempfile.TemporaryDirectory() as tmp:
            sock_path = str(Path(tmp) / "slow.sock")
            server = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
            server.bind(sock_path)
            server.listen(1)
            release = threading.Event()

            def _slow_server() -> None:
                conn, _ = server.accept()
                conn.recv(4096)  # request is delivered
                release.wait(5.0)  # never replies within the client timeout
                conn.close()

            thread = threading.Thread(target=_slow_server, daemon=True)
            thread.start()
            try:
                response = socket_send(sock_path, {"cmd": "run-job", "job": "x"}, timeout=0.5)
            finally:
                release.set()
                server.close()
                thread.join(timeout=5.0)

            self.assertIsNotNone(
                response,
                "request was delivered — must not be reported as unreachable",
            )
            self.assertEqual(response.get("status"), "timeout")

    def test_daemon_run_job_does_not_fall_back_after_delivery(self) -> None:
        from unittest import mock

        from second_brain import cli

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            config_path = config_dir / "memory.toml"
            config_path.write_text("")

            with (
                mock.patch.object(
                    cli, "socket_send", return_value={"status": "timeout"}
                ),
                mock.patch.object(cli, "run_job") as mock_run_job,
            ):
                exit_code = cli._daemon_run_job(str(config_path), "wiki_health_report")

            mock_run_job.assert_not_called()
            self.assertNotEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()

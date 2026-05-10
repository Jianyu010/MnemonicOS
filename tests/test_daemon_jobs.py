from __future__ import annotations

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
from second_brain.daemon import Daemon
from second_brain.daemon_status import daemon_status_summary
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import ingest_session
from second_brain.jobs import run_job


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
            daily_consolidation = "0 3 * * *"
            weekly_hygiene = "0 4 * * 0"
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

            daemon.job_lock.acquire()
            try:
                busy = daemon.scheduler.run_job_now("status_report")
            finally:
                daemon.job_lock.release()
            self.assertEqual(busy["status"], "busy")

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
                    "vectors_bytes": 8192,
                    "graph_bytes": 512,
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
        self.assertIn("vectors: 8.0 KB", text)


if __name__ == "__main__":
    unittest.main()

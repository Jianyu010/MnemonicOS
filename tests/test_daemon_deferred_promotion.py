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
from second_brain.ingest import _extract_error_aliases, _item_context_text, _read_memory_items, _synthesize_summary, ingest_session
from second_brain.jobs import run_job


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class DaemonDeferredPromotionTest(unittest.TestCase):
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

            [daemon]
            socket_path = "{(workspace / 'brain/data/mnemonic.sock').as_posix()}"
            pid_path = "{(workspace / 'brain/data/mnemonic.pid').as_posix()}"
            log_path = "{(workspace / 'brain/logs/daemon.log').as_posix()}"
            max_pending = 0
            max_oldest_age_minutes = 0

            [daemon.schedules]
            process_promotion_queue = "*/5 * * * *"
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

    def test_intake_queues_without_canonical_mutation_or_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            result = ingest_session(
                config,
                bridge_id="automation",
                session_id="queue-only",
                content=textwrap.dedent(
                    """
                    task_kind: knowledge

                    ## Task
                    Explain CANN NPU

                    ```memory
                    items:
                      - type: concept
                        title: "CANN NPU"
                        body: "CANN is Huawei's software stack for Ascend NPUs."
                        confidence: high
                    ```
                    """
                ).strip()
                + "\n",
            )
            self.assertEqual(result.promoted_notes, [])
            connection = connect_db(config.paths.db_path)
            queue_row = connection.execute(
                "SELECT status FROM promotion_queue WHERE archive_id = ?",
                (result.archive_id,),
            ).fetchone()
            note_count = connection.execute("SELECT COUNT(*) AS count FROM notes").fetchone()
            vector_count = connection.execute("SELECT COUNT(*) AS count FROM archive_chunk_vectors").fetchone()
            connection.close()
            self.assertEqual(str(queue_row["status"]), "pending")
            self.assertEqual(int(note_count["count"]), 0)
            self.assertEqual(int(vector_count["count"]), 0)

    def test_process_queue_promotes_later_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            ingest_session(
                config,
                bridge_id="automation",
                session_id="promote-later",
                content=textwrap.dedent(
                    """
                    task_kind: debugging
                    chain_id: trace/auth-recovery
                    chain_confidence: high

                    ## Task
                    Recover auth rollout

                    ```memory
                    items:
                      - type: incident
                        title: "Auth rollout failed on schema drift"
                        body: "First retry failed before schema reconciliation."
                        confidence: high
                      - type: procedure
                        title: "Recover auth rollout"
                        body: "Reconcile schema state before rerunning deploy."
                        confidence: high
                    ```
                    """
                ).strip()
                + "\n",
            )
            first = run_job(config, job_name="process_promotion_queue", dry_run=False)
            second = run_job(config, job_name="process_promotion_queue", dry_run=False)
            self.assertGreaterEqual(first.stats["promoted_notes"], 2)
            self.assertEqual(second.stats["pending_before"], 0)
            connection = connect_db(config.paths.db_path)
            queue_status = connection.execute(
                "SELECT status FROM promotion_queue WHERE session_id = 'promote-later'"
            ).fetchone()
            note_count = connection.execute(
                "SELECT COUNT(*) AS count FROM notes WHERE chain_id = 'trace/auth-recovery'"
            ).fetchone()
            connection.close()
            self.assertEqual(str(queue_status["status"]), "processed")
            self.assertEqual(int(note_count["count"]), 2)

    def test_promotion_enrichment_prefers_item_nuance_and_next_over_steps(self) -> None:
        content = textwrap.dedent(
            """
            ```memory
            items:
              - type: procedure
                title: "Use run_python: exit 1"
                body: |
                  Nuance: exit 1
                  Next: Use `read_file` before retrying.
                steps:
                  - "Run the same command again."
                  - "Avoid: Ignore the import path."
                confidence: high
            ```
            """
        ).strip()
        item = _read_memory_items(content, explicit_only=False)[0]

        self.assertEqual(
            _synthesize_summary(item, _item_context_text(item)),
            "WHEN exit 1 \u2192 Use `read_file` before retrying.",
        )

    def test_promotion_enrichment_extracts_item_error_aliases(self) -> None:
        content = textwrap.dedent(
            """
            ```memory
            items:
              - type: procedure
                title: "Use run_python after ModuleNotFoundError"
                steps:
                  - "Inspect files before rerun."
                confidence: high
            ```
            """
        ).strip()
        item = _read_memory_items(content, explicit_only=False)[0]

        self.assertIn("ModuleNotFoundError", _extract_error_aliases(item, _item_context_text(item)))

    def test_process_queue_uses_item_scoped_summary_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            ingest_session(
                config,
                bridge_id="ai_firm",
                session_id="nuance-enrichment",
                content=textwrap.dedent(
                    """
                    # AI Firm tool experience: run_python

                    Nuance: ModuleNotFoundError
                    Next: Run tests from project root with python -m pytest.

                    ```memory
                    items:
                      - type: procedure
                        title: "Use run_python: ModuleNotFoundError"
                        steps:
                          - "Blindly edit calculator logic."
                          - "Avoid: Repeat same test command unchanged."
                        confidence: high
                    ```
                    """
                ).strip()
                + "\n",
            )

            run_job(config, job_name="process_promotion_queue", dry_run=False)

            connection = connect_db(config.paths.db_path)
            row = connection.execute(
                "SELECT id, summary FROM notes WHERE title = ?",
                ("Use run_python: ModuleNotFoundError",),
            ).fetchone()
            alias = connection.execute(
                "SELECT note_id FROM aliases WHERE alias = ?",
                ("ModuleNotFoundError",),
            ).fetchone()
            connection.close()

            self.assertIsNotNone(row)
            self.assertEqual(
                str(row["summary"]),
                "Blindly edit calculator logic. (NOT: Repeat same test command unchanged.)",
            )
            self.assertEqual(str(alias["note_id"]), str(row["id"]))

    def test_process_queue_does_not_leak_session_signals_between_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            ingest_session(
                config,
                bridge_id="ai_firm",
                session_id="multi-item-nuance",
                content=textwrap.dedent(
                    """
                    # AI Firm tool experience: mixed notes

                    Nuance: ModuleNotFoundError
                    Next: Run tests from project root with python -m pytest.

                    ```memory
                    items:
                      - type: procedure
                        title: "Use run_python after failure"
                        steps:
                          - "Inspect files before rerun."
                        confidence: high
                      - type: concept
                        title: "Release note tone"
                        body: "Keep release notes concise and user-facing."
                        confidence: high
                    ```
                    """
                ).strip()
                + "\n",
            )

            run_job(config, job_name="process_promotion_queue", dry_run=False)

            connection = connect_db(config.paths.db_path)
            procedure = connection.execute(
                "SELECT id, summary FROM notes WHERE title = ?",
                ("Use run_python after failure",),
            ).fetchone()
            concept = connection.execute(
                "SELECT id, summary FROM notes WHERE title = ?",
                ("Release note tone",),
            ).fetchone()
            leaked_alias = connection.execute(
                """
                SELECT note_id FROM aliases
                WHERE alias = ? AND note_id IN (?, ?)
                """,
                ("ModuleNotFoundError", str(procedure["id"]), str(concept["id"])),
            ).fetchall()
            connection.close()

            self.assertEqual(str(procedure["summary"]), "Inspect files before rerun.")
            self.assertEqual(str(concept["summary"]), "Keep release notes concise and user-facing.")
            self.assertEqual(leaked_alias, [])

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

    def test_daemon_control_plane_reports_status_and_runs_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            ingest_session(
                config,
                bridge_id="automation",
                session_id="daemon-pending",
                content=textwrap.dedent(
                    """
                    task_kind: knowledge

                    ## Task
                    Explain daemon queue

                    ```memory
                    items:
                      - type: concept
                        title: "Daemon queue concept"
                        body: "Queued sessions are promoted later."
                        confidence: high
                    ```
                    """
                ).strip()
                + "\n",
            )

            daemon = Daemon(config)
            status = daemon.socket_server._handle_request({"cmd": "status"})
            self.assertEqual(status["status"], "ok")
            self.assertGreaterEqual(status["health"]["queue"]["pending_count"], 1)
            self.assertTrue(status["health"]["queue"]["warnings"])

            response = daemon.socket_server._handle_request({"cmd": "run-job", "job": "process_promotion_queue"})
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["result"]["status"], "ok")

            daemon.job_lock.acquire()
            try:
                busy = daemon.scheduler.run_job_now("status_report")
            finally:
                daemon.job_lock.release()
            self.assertEqual(busy["status"], "busy")

    def test_daemon_status_summary_includes_units(self) -> None:
        response = {
            "status": "ok",
            "service": {
                "running_job": None,
                "scheduled_jobs": [
                    {"name": "process_promotion_queue", "next_run_utc": "2026-04-17T12:00:00Z", "schedule": "*/5 * * * *"},
                ],
            },
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
                    {"job_name": "process_promotion_queue", "status": "ok", "finished_at": "2026-04-17 11:00:00"},
                ],
            },
        }
        text = daemon_status_summary(response)
        self.assertIn("pending: 3 item(s)", text)
        self.assertIn("errors: 1 item(s)", text)
        self.assertIn("oldest pending age: 2 hr 5 min", text)
        self.assertIn("db: 1.5 KB", text)
        self.assertIn("vectors: 8.0 KB", text)


    def test_stuck_processing_row_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._prepare_workspace(Path(tmp_dir))
            result = ingest_session(
                config,
                bridge_id="automation",
                session_id="stuck-processing",
                content=textwrap.dedent(
                    """
                    task_kind: knowledge

                    ## Task
                    Explain stuck-row recovery

                    ```memory
                    items:
                      - type: concept
                        title: "Stuck processing recovery"
                        body: "The worker resets processing rows to pending on startup."
                        confidence: high
                    ```
                    """
                ).strip()
                + "\n",
            )
            # Simulate a crashed prior run by manually marking the row 'processing'.
            connection = connect_db(config.paths.db_path)
            with connection:
                connection.execute(
                    "UPDATE promotion_queue SET status = 'processing', last_error = 'simulated interrupted run' WHERE archive_id = ?",
                    (result.archive_id,),
                )
            connection.close()

            job_result = run_job(config, job_name="process_promotion_queue", dry_run=False)

            self.assertGreaterEqual(job_result.stats["recovered_processing_rows"], 1)

            connection = connect_db(config.paths.db_path)
            final_status = connection.execute(
                "SELECT status FROM promotion_queue WHERE archive_id = ?",
                (result.archive_id,),
            ).fetchone()
            note_count = connection.execute("SELECT COUNT(*) AS count FROM notes").fetchone()
            connection.close()

            self.assertIn(str(final_status["status"]), {"processed", "skipped"})
            self.assertGreater(int(note_count["count"]), 0)


if __name__ == "__main__":
    unittest.main()

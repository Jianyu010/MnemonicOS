from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import shlex
import socket
import subprocess
import threading
from typing import Any

from .config import AppConfig, default_config_path, load_config
from .daemon_jobs import JobRegistry, build_registry
from .daemon_processes import DaemonProcessRegistry
from .jobs import collect_status_snapshot
from .models import JobRunResult


class Scheduler:
    def __init__(
        self,
        config: AppConfig,
        registry: JobRegistry,
        shutdown_event: threading.Event,
        process_registry: DaemonProcessRegistry | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.shutdown_event = shutdown_event
        self.process_registry = process_registry or DaemonProcessRegistry()
        self.logger = logging.getLogger("mnemonicos.daemon.scheduler")

        # Per-job locks — prevent the same job from running twice concurrently.
        self._job_locks: dict[str, threading.Lock] = {
            name: threading.Lock() for name in registry.job_names()
        }
        # Protects _running, _job_progress, and _last_results.
        self._state_lock = threading.Lock()
        # Currently running job threads: job_name -> Thread.
        self._running: dict[str, threading.Thread] = {}
        # Live progress dicts, keyed by job name (currently only extraction uses this).
        self._job_progress: dict[str, dict[str, Any]] = {}
        # Most-recent result per job.
        self._last_results: dict[str, dict[str, Any]] = {}

    def _get_job_lock(self, job_name: str) -> threading.Lock:
        if job_name not in self._job_locks:
            self._job_locks[job_name] = threading.Lock()
        return self._job_locks[job_name]

    def _run_job_in_thread(self, job_name: str, job_lock: threading.Lock) -> None:
        """Body of the per-job worker thread.  job_lock is already acquired on entry."""
        progress: dict[str, Any] | None = {} if job_name == "process_extraction_queue" else None
        if progress is not None:
            with self._state_lock:
                self._job_progress[job_name] = progress
        try:
            result = self.registry.run(
                self.config,
                job_name,
                progress=progress,
                process_registry=self.process_registry,
            )
            payload: dict[str, Any] = {
                "status": "ok",
                "job": job_name,
                "result": {
                    "job_name": result.job_name,
                    "status": result.status,
                    "stats": result.stats,
                    "review_items_created": result.review_items_created,
                },
            }
            with self._state_lock:
                self._last_results[job_name] = payload
        except Exception as exc:
            self.logger.exception("job failed: %s", job_name)
            payload = {"status": "error", "job": job_name, "error": str(exc)}
            with self._state_lock:
                self._last_results[job_name] = payload
        finally:
            job_lock.release()
            with self._state_lock:
                self._running.pop(job_name, None)
                self._job_progress.pop(job_name, None)

    def _spawn_job(self, job_name: str, job_lock: threading.Lock) -> threading.Thread:
        """Spawn a daemon thread for job_name.  job_lock must already be acquired."""
        t = threading.Thread(
            target=self._run_job_in_thread,
            args=(job_name, job_lock),
            name=f"mnemonicos-job-{job_name}",
            daemon=True,
        )
        with self._state_lock:
            self._running[job_name] = t
        t.start()
        return t

    def tick(self) -> None:
        """Fire any due jobs, each in its own thread.  Returns immediately."""
        now = datetime.now(timezone.utc)
        for entry in self.registry.due_jobs(now):
            job_lock = self._get_job_lock(entry.name)
            if not job_lock.acquire(blocking=False):
                self.logger.debug("job %s already running, skipping tick", entry.name)
                continue
            self._spawn_job(entry.name, job_lock)

    def run(self) -> None:
        while not self.shutdown_event.is_set():
            self.tick()
            self.shutdown_event.wait(30.0)

    def run_job_now(self, job_name: str) -> dict[str, Any]:
        """Trigger a job immediately.  Runs synchronously in the calling thread."""
        if job_name not in self.registry.job_names():
            return {"status": "error", "error": f"unknown job: {job_name}"}
        job_lock = self._get_job_lock(job_name)
        if not job_lock.acquire(blocking=False):
            with self._state_lock:
                running = list(self._running.keys())
            return {"status": "busy", "running_job": job_name, "running_jobs": running}
        progress: dict[str, Any] | None = {} if job_name == "process_extraction_queue" else None
        if progress is not None:
            with self._state_lock:
                self._job_progress[job_name] = progress
        try:
            result = self.registry.run(
                self.config,
                job_name,
                progress=progress,
                process_registry=self.process_registry,
            )
            payload: dict[str, Any] = {
                "status": "ok",
                "job": job_name,
                "result": {
                    "job_name": result.job_name,
                    "status": result.status,
                    "stats": result.stats,
                    "review_items_created": result.review_items_created,
                },
            }
            with self._state_lock:
                self._last_results[job_name] = payload
            return payload
        except Exception as exc:
            self.logger.exception("job failed: %s", job_name)
            payload = {"status": "error", "job": job_name, "error": str(exc)}
            with self._state_lock:
                self._last_results[job_name] = payload
            return payload
        finally:
            job_lock.release()
            with self._state_lock:
                self._job_progress.pop(job_name, None)

    def join_running_jobs(self, timeout: float = 30.0) -> None:
        """Wait for all in-flight job threads to finish (used during shutdown)."""
        with self._state_lock:
            threads = list(self._running.values())
        for t in threads:
            t.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            running_jobs = list(self._running.keys())
            progress = dict(self._job_progress.get("process_extraction_queue") or {})
            # Backward-compat scalar: prefer extraction if running, else first job, else None.
            running_job: str | None = (
                "process_extraction_queue"
                if "process_extraction_queue" in self._running
                else (running_jobs[0] if running_jobs else None)
            )
            last_result = self._last_results.get(running_job) if running_job else None
        scheduled_jobs = [
            {
                "name": entry.name,
                "schedule": entry.schedule,
                "next_run_utc": entry.next_due.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
            for entry in self.registry.all_jobs()
        ]
        return {
            "running_job": running_job,
            "running_jobs": running_jobs,
            "job_progress": progress if progress else None,
            "scheduled_jobs": scheduled_jobs,
            "last_result": last_result,
        }


class SocketServer:
    def __init__(self, config: AppConfig, scheduler: Scheduler, shutdown_event: threading.Event) -> None:
        self.config = config
        self.scheduler = scheduler
        self.shutdown_event = shutdown_event
        self.logger = logging.getLogger("mnemonicos.daemon.socket")
        self.server_socket: socket.socket | None = None
        self._bound = False  # True only after we successfully bind the socket

    def _remove_stale_socket(self) -> None:
        socket_path = self.config.daemon.socket_path if self.config.daemon is not None else None
        if socket_path is None:
            return
        try:
            if socket_path.exists():
                socket_path.unlink()
        except FileNotFoundError:
            pass

    def _handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        cmd = str(payload.get("cmd", "")).strip()
        if cmd == "status":
            snapshot = collect_status_snapshot(self.config)
            return {
                "status": "ok",
                "service": self.scheduler.status(),
                "health": snapshot,
            }
        if cmd == "run-job":
            job_name = str(payload.get("job", "")).strip()
            return self.scheduler.run_job_now(job_name)
        if cmd == "shutdown":
            self.shutdown_event.set()
            return {"status": "ok", "message": "shutdown_started"}
        return {"status": "error", "error": f"unsupported command: {cmd}"}

    def serve(self) -> None:
        if self.config.daemon is None:
            raise RuntimeError("daemon config missing")
        socket_path = self.config.daemon.socket_path
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_socket()
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket = server_socket
        server_socket.bind(str(socket_path))
        self._bound = True
        server_socket.listen()
        server_socket.settimeout(1.0)
        while not self.shutdown_event.is_set():
            try:
                client, _ = server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                try:
                    request = b""
                    while b"\n" not in request:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        request += chunk
                    payload = json.loads(request.decode("utf-8").strip() or "{}")
                    response = self._handle_request(payload)
                except Exception as exc:
                    response = {"status": "error", "error": str(exc)}
                try:
                    client.sendall((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))
                except OSError:
                    pass
        self.close()

    def close(self) -> None:
        if self.server_socket is not None:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None
        # Only remove the socket file if we were the one who created it.
        # Skipping this prevents a dying sibling from nuking a live daemon's socket.
        if self._bound:
            self._remove_stale_socket()


class Daemon:
    def __init__(self, config: AppConfig, registry: JobRegistry | None = None) -> None:
        if config.daemon is None:
            raise RuntimeError("daemon config missing")
        self.config = config
        self.registry = registry or build_registry(config)
        self.shutdown_event = threading.Event()
        self.process_registry = DaemonProcessRegistry()
        self.scheduler = Scheduler(config, self.registry, self.shutdown_event, self.process_registry)
        self.socket_server = SocketServer(config, self.scheduler, self.shutdown_event)
        self.scheduler_thread = threading.Thread(target=self.scheduler.run, name="mnemonicos-scheduler", daemon=True)
        self.socket_thread = threading.Thread(target=self.socket_server.serve, name="mnemonicos-socket", daemon=True)
        self.logger = logging.getLogger("mnemonicos.daemon")

    def _setup_logging(self) -> None:
        log_path = self.config.daemon.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers.clear()
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        file_handler = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=5)
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        root.addHandler(stream_handler)

    def _write_pid(self) -> None:
        pid_path = self.config.daemon.pid_path
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    def _remove_pid(self) -> None:
        pid_path = self.config.daemon.pid_path
        try:
            current = pid_path.read_text(encoding="utf-8").strip()
            if current != str(os.getpid()):
                return  # Another daemon owns this file; don't delete it.
            pid_path.unlink()
        except (FileNotFoundError, OSError):
            pass

    def _mark_processing_items_as_error(self) -> None:
        """On shutdown, mark any items still 'processing' as error.

        Subprocess-based extraction means no item should be processing after
        the scheduler thread joins — but if one was killed mid-run (e.g. OOM,
        SIGKILL on the subprocess), the row stays stuck. Clearing it here lets
        retry_failed_extractions tombstone them on the next daemon run instead
        of waiting for the 60-minute stale timeout.
        """
        try:
            from .db import connect_db, run_migrations
            conn = connect_db(self.config.paths.db_path)
            run_migrations(conn, self.config.paths.workspace_root / "migrations")
            with conn:
                conn.execute(
                    """
                    UPDATE extraction_queue
                    SET status = 'error',
                        finished_at = datetime('now'),
                        last_error = 'daemon shutdown: item was still processing at exit'
                    WHERE status = 'processing'
                    """
                )
            self.logger.info("cleared in-flight processing items on shutdown")
        except Exception as exc:
            self.logger.warning("failed to clear processing items on shutdown: %s", exc)

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:
            self.logger.info("received signal %s, shutting down", signum)
            self.shutdown_event.set()

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def _start_managed_ollama(self) -> None:
        daemon_config = self.config.daemon
        if daemon_config is None or not daemon_config.ollama_managed:
            return
        command = daemon_config.ollama_command.strip()
        if not command:
            self.logger.warning("ollama_managed is true but ollama_command is empty; not starting Ollama")
            return
        proc = subprocess.Popen(
            shlex.split(command),
            cwd=str(self.config.paths.workspace_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.process_registry.register(proc, label="ollama", process_group=True)
        self.logger.info("started managed Ollama process pid=%s", proc.pid)

    def run(self) -> int:
        self._setup_logging()
        self._write_pid()
        if threading.current_thread() is threading.main_thread():
            self._install_signal_handlers()
        self.logger.info("mnemonicos daemon starting")
        self._start_managed_ollama()
        try:
            self.scheduler_thread.start()
            self.socket_thread.start()
            while not self.shutdown_event.is_set():
                self.shutdown_event.wait(1.0)
        finally:
            self.shutdown_event.set()
            self.scheduler_thread.join(timeout=5.0)
            self.process_registry.stop_all(grace_seconds=5.0, kill_seconds=5.0)
            # Wait for in-flight job threads before cleaning up DB state.
            self.scheduler.join_running_jobs(timeout=30.0)
            self.process_registry.stop_all(grace_seconds=5.0, kill_seconds=5.0)
            self.socket_server.close()
            self.socket_thread.join(timeout=2.0)
            self._mark_processing_items_as_error()
            self._remove_pid()
            self.logger.info("mnemonicos daemon stopped")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MnemonicOS daemon")
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to the TOML config file. "
            "Defaults to config/memory.toml when present, otherwise config/memory.example.toml."
        ),
    )
    args = parser.parse_args(argv)
    if args.config is None:
        args.config = str(default_config_path())
    config = load_config(args.config)
    daemon = Daemon(config)
    return daemon.run()


if __name__ == "__main__":
    raise SystemExit(main())

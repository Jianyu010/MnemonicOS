"""Push-triggered intake pipeline.

Called by every intake path (ingest_artifact, ingest_session, end_episode)
immediately after material is committed to the true-raw archive. Runs
extraction and promotion without waiting for a daemon cron tick.

When the daemon is running: asks the daemon socket to run extraction, then
promotion, and returns after both jobs complete.

When the daemon is not running: runs extraction then promotion inline,
blocking the caller until both complete.

The daemon's scheduled extraction and promotion jobs are cleanup/retry only:
they handle errors and stale states, not primary processing. The daemon
cannot scan true-raw for unprocessed items — missing the push trigger at
intake means an item is permanently invisible to the daemon.
"""
from __future__ import annotations

import json
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AppConfig


def _run_daemon_job(socket_path: object, job_name: str) -> bool:
    """Send run-job to daemon socket and wait for a successful response.

    Returns True if the daemon completed the job. Busy/error responses are
    treated as not handled so the caller can choose a fallback.
    """
    payload = json.dumps({"cmd": "run-job", "job": job_name}) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(60.0)
            client.connect(str(socket_path))
            client.sendall(payload.encode("utf-8"))
            response = b""
            while b"\n" not in response:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response += chunk
        result = json.loads(response.decode("utf-8").strip() or "{}")
        return result.get("status") == "ok"
    except (OSError, json.JSONDecodeError, TimeoutError):
        return False


def trigger_embed_backfill(config: "AppConfig") -> None:
    """Run embed_backfill promptly, using the daemon when available."""
    if config.daemon is not None:
        delivered = _run_daemon_job(config.daemon.socket_path, "embed_backfill")
        if delivered:
            return
    from .jobs import run_job
    run_job(config, job_name="embed_backfill", dry_run=False)


def trigger_pipeline(config: "AppConfig") -> None:
    """Trigger extraction (and promotion) immediately after an intake event.

    Preferred path: run extraction and promotion through the daemon socket.
    Fallback path (daemon not configured or not reachable): run extraction
    then promotion inline.
    """
    if config.daemon is not None:
        extracted = _run_daemon_job(
            config.daemon.socket_path,
            "process_extraction_queue",
        )
        promoted = extracted and _run_daemon_job(
            config.daemon.socket_path,
            "process_promotion_queue",
        )
        if promoted:
            return
    from .extraction.worker import process_extraction_queue
    from .promotion.worker import process_promotion_queue
    process_extraction_queue(config)
    process_promotion_queue(config)

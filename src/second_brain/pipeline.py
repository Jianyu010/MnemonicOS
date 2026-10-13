"""Push-triggered intake pipeline.

Called by every intake path (ingest_artifact, ingest_session, end_episode)
immediately after material is committed to the true-raw archive, so extraction
and promotion start without waiting for a daemon cron tick.

When the daemon is reachable: asks the daemon socket to *start* extraction and
promotion (``run-job`` with ``wait: false``) and returns as soon as the daemon
acknowledges. A ``busy`` reply means the daemon is already running that job;
the newly queued item is picked up by that run (quick lane keeps draining) or
by the next scheduled tick. Either way the daemon owns the work — the caller
never runs a second extraction loop beside it.

When the daemon is not reachable at all (socket missing / connection refused):
runs extraction then promotion inline, blocking the caller until both complete.

The daemon's scheduled extraction and promotion jobs remain the safety net for
anything the push trigger missed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .daemon_status import socket_send

if TYPE_CHECKING:
    from .config import AppConfig

# The daemon replies to ``wait: false`` immediately, so a short window is enough.
_DAEMON_REPLY_TIMEOUT_SECONDS = 10.0


def _run_daemon_job(socket_path: object, job_name: str) -> bool:
    """Ask the daemon to start ``job_name``.

    Returns True when the daemon took responsibility for the job: it started
    it (``started``), it was already running it (``busy``), it ran it to
    completion (``ok``), or the request was delivered but the reply was slow
    (``timeout``). Returns False only when the daemon is unreachable, which is
    the one case where the caller should do the work itself.
    """
    response = socket_send(
        socket_path,
        {"cmd": "run-job", "job": job_name, "wait": False},
        timeout=_DAEMON_REPLY_TIMEOUT_SECONDS,
    )
    if response is None:
        return False
    return response.get("status") in {"started", "busy", "ok", "timeout"}


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

    Preferred path: hand both jobs to the daemon and return. Fallback path
    (daemon not configured or not reachable): run extraction then promotion
    inline.
    """
    if config.daemon is not None:
        extracted = _run_daemon_job(config.daemon.socket_path, "process_extraction_queue")
        if extracted:
            # Promotion is requested as well so candidates that are already
            # promotion_ready move on now; the scheduled job covers the rest.
            _run_daemon_job(config.daemon.socket_path, "process_promotion_queue")
            return
    from .extraction.worker import process_extraction_queue
    from .promotion.worker import process_promotion_queue
    process_extraction_queue(config)
    process_promotion_queue(config)

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from second_brain.config import AppConfig
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import _migrations_dir
from second_brain.projection.pipeline import ProjectionService
from second_brain.projection.pipeline_models import ProjectionOptions

from .models import ExtractionProcessResult, ModelCandidate, VisualSnippet
from .providers import build_llm_provider, build_vlm_provider, make_llm_packet
from .queue import (
    mark_error,
    mark_finished,
    mark_processing,
    pending_items,
    pending_quick_items,
    pending_slow_items,
    queue_row_to_source_ref,
    requeue_orphaned_processing,
    source_ref_id,
    update_window_checkpoint,
)
from .store import (
    append_candidates,
    candidate_counts,
    load_visual_snippets,
    replace_candidates,
    upsert_visual_snippet,
)
from .validation import parse_candidate_bundle, parse_intra_source_relations, projection_evidence_ids, validate_candidate

_log = logging.getLogger(__name__)

# How long the quick lane waits between empty refills while the slow lane is
# still running (dual-lane mode). Module-level so tests can shorten it.
_QUICK_LANE_REFILL_WAIT_SECONDS = 15.0

# Warning prefix on placeholder snippets for artifacts past the per-item VLM cap.
_VLM_SKIPPED_WARNING_PREFIX = "visual_pass_skipped:"


def _terminate_process_group(proc: subprocess.Popen, *, grace_seconds: float = 2.0) -> None:
    """SIGTERM then SIGKILL the subprocess's process group, and reap it.

    Reaping uses ``proc.wait()`` rather than ``proc.communicate()``: the caller
    (``_LaneHandle.join``) runs a separate thread that drains ``proc.stderr``, and
    two threads reading the same pipe corrupts ``communicate()`` — it could raise
    a non-``TimeoutExpired`` error, skip the SIGKILL escalation, and leave a live
    orphan subprocess that a later tick then re-dispatches (double-spawn, see
    brain/system/debug-ingestion.md B3/B5). ``wait()`` only reaps; it never
    touches the pipes, so it cannot race the drain thread.

    The process is a session/group leader (``start_new_session=True``), so its
    pgid equals its pid and ``os.killpg(proc.pid, ...)`` targets the whole group.
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _log.error("subprocess pid=%s survived SIGKILL — possible orphan", proc.pid)


@dataclass
class _LaneHandle:
    """Non-blocking handle for a single extraction subprocess lane.

    Caller spawns the subprocess, receives a handle, and later calls
    ``join()`` to block until completion or timeout.

    The subprocess is killed when either:
    - its progress file stops changing for ``stall_timeout_seconds`` (stall), or
    - the absolute ``timeout_seconds`` deadline is exceeded (safety net).
    """

    proc: subprocess.Popen
    poll_thread: "threading.Thread | None"
    stop_poll: threading.Event
    progress_file: Path
    timeout_seconds: int
    stall_timeout_seconds: int
    process_registry: "Any | None"

    def join(self) -> bool:
        """Block until subprocess completes or is killed. Returns True if killed."""
        stderr_chunks: list[bytes] = []

        def _drain_stderr() -> None:
            if self.proc.stderr is not None:
                try:
                    while True:
                        chunk = self.proc.stderr.read(4096)
                        if not chunk:
                            break
                        stderr_chunks.append(chunk)
                except (ValueError, OSError):
                    # Pipe closed underneath us during teardown — expected.
                    pass

        drain_thread = threading.Thread(target=_drain_stderr, daemon=True)
        drain_thread.start()

        last_progress_bytes: bytes = b""
        last_progress_time = time.monotonic()
        deadline = time.monotonic() + self.timeout_seconds

        try:
            while True:
                if self.proc.poll() is not None:
                    drain_thread.join(timeout=2.0)
                    stderr = b"".join(stderr_chunks)
                    if self.proc.returncode != 0:
                        _log.warning(
                            "extraction subprocess (progress_file=%s) exited %s: %s",
                            self.progress_file.name,
                            self.proc.returncode,
                            stderr.decode("utf-8", errors="replace")[:300],
                        )
                    return False

                now = time.monotonic()

                if now > deadline:
                    _log.warning(
                        "extraction subprocess (progress_file=%s) exceeded absolute limit %ss — killing",
                        self.progress_file.name,
                        self.timeout_seconds,
                    )
                    _terminate_process_group(self.proc)
                    drain_thread.join(timeout=2.0)
                    return True

                try:
                    cur = self.progress_file.read_bytes()
                    if cur != last_progress_bytes:
                        last_progress_bytes = cur
                        last_progress_time = now
                except FileNotFoundError:
                    pass

                if now - last_progress_time > self.stall_timeout_seconds:
                    _log.warning(
                        "extraction subprocess (progress_file=%s) stalled — no progress for %ss — killing",
                        self.progress_file.name,
                        self.stall_timeout_seconds,
                    )
                    _terminate_process_group(self.proc)
                    drain_thread.join(timeout=2.0)
                    return True

                time.sleep(1.0)
        finally:
            self.stop_poll.set()
            if self.poll_thread is not None:
                self.poll_thread.join(timeout=2.0)
            # Safety net: never return while the subprocess is still alive. If the
            # monitoring loop above raised (or any non-timeout exit path is taken),
            # the normal kill branches are skipped; without this an orphaned live
            # subprocess is left behind, the job lock is released, and a later tick
            # re-dispatches the same item — spawning a second subprocess and bumping
            # attempt_count (double-spawn, see brain/system/debug-ingestion.md B3/B5).
            if self.proc.poll() is None:
                _terminate_process_group(self.proc)
            drain_thread.join(timeout=2.0)
            if self.process_registry is not None:
                self.process_registry.unregister(self.proc)
            # Close the pipes we opened so the FDs are not leaked (see B2). Safe now
            # that the process is reaped and the drain thread has joined.
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            try:
                self.progress_file.unlink()
            except FileNotFoundError:
                pass


def _make_extraction_proc(
    *,
    config_path: str | None,
    item_id: int,
    progress_file: Path,
) -> subprocess.Popen:
    """Build and launch extraction subprocess. Returns Popen (not waited)."""
    cmd = [
        sys.executable, "-m", "second_brain.extraction._subprocess_entry",
        "--item-id", str(item_id),
        "--progress-file", str(progress_file),
    ]
    if config_path:
        cmd.extend(["--config", config_path])
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _spawn_item_subprocess(
    *,
    config_path: str | None,
    item_id: int,
    timeout_seconds: int,
    stall_timeout_seconds: int,
    progress: "dict[str, Any] | None",
    item_index: int,
    total_items: int,
    item_name: str,
    process_registry: "Any | None" = None,
    progress_key: "str | None" = None,
) -> _LaneHandle:
    """Spawn extraction subprocess; return handle immediately (non-blocking).

    ``progress_key`` routes progress updates into a sub-dict of *progress*
    (e.g. ``"quick"`` or ``"slow"`` for dual-lane mode).  ``None`` means
    flat/single-lane mode — updates go directly into *progress*.
    """
    # Use a unique suffix so concurrent or double-spawned processes for the same
    # item_id each get an isolated file. A shared path causes stall detection
    # cross-contamination: progress writes from either process reset both timers
    # (see brain/system/debug-ingestion.md B3).
    progress_file = Path(tempfile.gettempdir()) / f"mnemo_progress_{item_id}_{uuid.uuid4().hex[:8]}.json"
    try:
        progress_file.unlink()
    except FileNotFoundError:
        pass

    if progress is not None:
        update: dict[str, Any] = {
            "item_id": item_id,
            "item_name": item_name,
            "item_index": item_index,
            "total_items": total_items,
            "phase": "preparing",
            "window": 0,
            "total_windows": 0,
        }
        if progress_key is not None:
            if not isinstance(progress.get(progress_key), dict):
                progress[progress_key] = {}
            progress[progress_key].update(update)
        else:
            progress.update(update)

    proc = _make_extraction_proc(
        config_path=config_path,
        item_id=item_id,
        progress_file=progress_file,
    )
    if process_registry is not None:
        process_registry.register(proc, label=f"extraction-item-{item_id}", process_group=True)

    stop_poll = threading.Event()

    def _poll() -> None:
        while not stop_poll.is_set():
            if progress_file.exists():
                try:
                    data = json.loads(progress_file.read_text())
                    if progress_key is not None and progress is not None:
                        if not isinstance(progress.get(progress_key), dict):
                            progress[progress_key] = {}
                        progress[progress_key].update(data)
                    elif progress is not None:
                        progress.update(data)
                except Exception:
                    pass
            stop_poll.wait(1.5)

    if progress is not None:
        poll_thread = threading.Thread(target=_poll, daemon=True)
        poll_thread.start()
    else:
        poll_thread = None

    return _LaneHandle(
        proc=proc,
        poll_thread=poll_thread,
        stop_poll=stop_poll,
        progress_file=progress_file,
        timeout_seconds=timeout_seconds,
        stall_timeout_seconds=stall_timeout_seconds,
        process_registry=process_registry,
    )


def process_extraction_queue(
    config: AppConfig,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    progress: dict[str, Any] | None = None,
    process_registry: Any | None = None,
) -> ExtractionProcessResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    if not dry_run:
        # Reap items wedged in 'processing' by a parent that died (e.g. daemon
        # restart) so they can be retried instead of blocking the queue forever.
        with connection:
            reaped = requeue_orphaned_processing(
                connection,
                older_than_seconds=config.extraction.max_extraction_item_seconds,
            )
        if reaped:
            _log.warning("requeued %d orphaned 'processing' extraction item(s)", reaped)
    stats = ExtractionProcessResult(
        processed=0,
        skipped=0,
        errors=0,
        windows=0,
        artifacts=0,
        visual_snippets=0,
        candidates=0,
        promotion_ready=0,
    )
    try:
        if not dry_run and config.extraction.dual_lane_enabled:
            _process_dual_lane(
                config, connection, stats,
                progress=progress,
                process_registry=process_registry,
            )
        else:
            _process_single_lane(
                config, connection, stats,
                dry_run=dry_run,
                limit=limit,
                progress=progress,
                process_registry=process_registry,
            )
    finally:
        if progress is not None:
            progress.clear()
        connection.close()
    return stats


def _process_single_lane(
    config: AppConfig,
    connection,
    stats: ExtractionProcessResult,
    *,
    dry_run: bool,
    limit: "int | None",
    progress: "dict[str, Any] | None",
    process_registry: "Any | None",
) -> None:
    """Process extraction queue sequentially (single-lane mode, original behaviour)."""
    max_item_seconds = config.extraction.max_extraction_item_seconds
    config_path = str(config.config_path) if config.config_path is not None else None
    items = pending_items(connection, limit=limit or config.extraction.max_queue_batch)
    total_items = len(items)
    for item_index, item in enumerate(items):
        if progress is not None:
            progress.clear()
            progress.update({
                "item_id": item.id,
                "item_name": _progress_name(item),
                "item_index": item_index,
                "total_items": total_items,
                "window": 0,
                "total_windows": 0,
                "phase": "preparing",
            })
        if dry_run:
            # Dry-run: use in-process path (no subprocess overhead, no DB writes)
            service = ProjectionService(config)
            try:
                item_result = _process_item(config, service, connection, item=item, dry_run=True, progress=progress)
                stats.processed += item_result["processed"]
                stats.skipped += item_result["skipped"]
                stats.windows += item_result["windows"]
                stats.artifacts += item_result["artifacts"]
            except Exception as exc:
                _log.warning("dry-run item %s failed: %s", item.id, exc)
                stats.errors += 1
            continue

        with connection:
            claimed = mark_processing(connection, item.id)
        if not claimed:
            # Row was no longer 'pending' — already claimed elsewhere or a stale
            # orphan is still in flight. Skip to avoid a second live subprocess.
            _log.debug("extraction item %s no longer pending at claim; skipping", item.id)
            continue

        timed_out = _run_item_subprocess(
            config_path=config_path,
            item_id=item.id,
            timeout_seconds=max_item_seconds,
            stall_timeout_seconds=config.extraction.stall_timeout_seconds,
            progress=progress,
            item_index=item_index,
            total_items=total_items,
            item_name=_progress_name(item),
            process_registry=process_registry,
        )
        if timed_out:
            stats.errors += 1
            with connection:
                mark_error(
                    connection,
                    queue_id=item.id,
                    error=(
                        f"timeout: item exceeded limit "
                        f"(stall={config.extraction.stall_timeout_seconds}s, "
                        f"max={max_item_seconds}s)"
                    ),
                )
        else:
            _accumulate_subprocess_result(connection, stats, item.id)


def _process_dual_lane(
    config: AppConfig,
    connection,
    stats: ExtractionProcessResult,
    *,
    progress: "dict[str, Any] | None",
    process_registry: "Any | None",
) -> None:
    """Process extraction queue with independent quick-lane and slow-lane threads.

    Each thread processes its candidates serially using its own SQLite
    connection. The two threads run concurrently, so a slow-lane stall or
    timeout never blocks quick-lane progress.
    """
    max_item_seconds = config.extraction.max_extraction_item_seconds
    stall_seconds = config.extraction.stall_timeout_seconds
    config_path = str(config.config_path) if config.config_path is not None else None

    quick_candidates = pending_quick_items(connection, limit=config.extraction.quick_lane_batch)
    slow_candidates = pending_slow_items(connection, limit=config.extraction.slow_lane_batch)
    quick_total = len(quick_candidates)
    slow_total = len(slow_candidates)

    if progress is not None:
        progress.update({
            "mode": "dual_lane",
            "quick": {},
            "slow": {},
            "quick_total": quick_total,
            "slow_total": slow_total,
        })

    quick_stats = ExtractionProcessResult(0, 0, 0, 0, 0, 0, 0, 0)
    slow_stats = ExtractionProcessResult(0, 0, 0, 0, 0, 0, 0, 0)

    # Set when the slow lane has nothing left to do. The quick lane keeps
    # re-fetching newly queued sessions/episodes until then, so a two-hour PDF
    # no longer caps quick-lane throughput at quick_lane_batch items per run
    # (the scheduler holds the job lock for the whole run, so a tick cannot
    # start a second quick lane; see issue #38).
    slow_done = threading.Event()
    if not slow_candidates:
        slow_done.set()

    def _run_lane(
        candidates: list,
        lane_stats: ExtractionProcessResult,
        progress_key: str,
        lane_total: int,
        *,
        refill: "Callable[[Any], list] | None" = None,
        drain_until: "threading.Event | None" = None,
    ) -> None:
        conn = connect_db(config.paths.db_path)
        run_migrations(conn, _migrations_dir(config))
        seen_ids: set[int] = {item.id for item in candidates}
        queue: list = list(candidates)
        idx = 0
        try:
            while True:
                if not queue:
                    if refill is None or drain_until is None or drain_until.is_set():
                        break
                    fresh = [item for item in refill(conn) if item.id not in seen_ids]
                    if not fresh:
                        # Nothing new yet; wait for more intake or for the other
                        # lane to finish, whichever comes first, then look again.
                        drain_until.wait(_QUICK_LANE_REFILL_WAIT_SECONDS)
                        continue
                    seen_ids.update(item.id for item in fresh)
                    queue.extend(fresh)
                    lane_total += len(fresh)
                    if progress is not None:
                        progress[f"{progress_key}_total"] = lane_total
                item = queue.pop(0)
                with conn:
                    claimed = mark_processing(conn, item.id)
                if not claimed:
                    # Row was no longer 'pending' — already claimed elsewhere or a
                    # stale orphan is still in flight. Skip to avoid a second live
                    # subprocess for the same item.
                    _log.debug("extraction item %s no longer pending at claim; skipping", item.id)
                    idx += 1
                    continue
                handle = _spawn_item_subprocess(
                    config_path=config_path,
                    item_id=item.id,
                    timeout_seconds=max_item_seconds,
                    stall_timeout_seconds=stall_seconds,
                    progress=progress,
                    item_index=idx,
                    total_items=lane_total,
                    item_name=_progress_name(item),
                    process_registry=process_registry,
                    progress_key=progress_key,
                )
                idx += 1
                timed_out = handle.join()
                if timed_out:
                    lane_stats.errors += 1
                    with conn:
                        mark_error(
                            conn,
                            queue_id=item.id,
                            error=(
                                f"timeout: item exceeded limit "
                                f"(stall={stall_seconds}s, max={max_item_seconds}s)"
                            ),
                        )
                else:
                    _accumulate_subprocess_result(conn, lane_stats, item.id)
        finally:
            conn.close()

    def _run_slow_lane() -> None:
        try:
            _run_lane(slow_candidates, slow_stats, "slow", slow_total)
        finally:
            slow_done.set()

    def _run_quick_lane() -> None:
        _run_lane(
            quick_candidates,
            quick_stats,
            "quick",
            quick_total,
            refill=lambda conn: pending_quick_items(conn, limit=config.extraction.quick_lane_batch),
            drain_until=slow_done,
        )

    lane_threads: list[threading.Thread] = []
    if quick_candidates or slow_candidates:
        # Start the quick lane even when it is initially empty if the slow lane
        # has work: sessions queued during the PDF run are picked up right away.
        t = threading.Thread(
            target=_run_quick_lane,
            name="mnemonicos-extraction-quick",
            daemon=True,
        )
        t.start()
        lane_threads.append(t)
    if slow_candidates:
        t = threading.Thread(
            target=_run_slow_lane,
            name="mnemonicos-extraction-slow",
            daemon=True,
        )
        t.start()
        lane_threads.append(t)

    for t in lane_threads:
        t.join()

    for lane in (quick_stats, slow_stats):
        stats.processed += lane.processed
        stats.skipped += lane.skipped
        stats.errors += lane.errors
        stats.windows += lane.windows
        stats.artifacts += lane.artifacts
        stats.visual_snippets += lane.visual_snippets
        stats.candidates += lane.candidates
        stats.promotion_ready += lane.promotion_ready


def _run_item_subprocess(
    *,
    config_path: str | None,
    item_id: int,
    timeout_seconds: int,
    stall_timeout_seconds: int,
    progress: dict[str, Any] | None,
    item_index: int,
    total_items: int,
    item_name: str,
    process_registry: Any | None = None,
) -> bool:
    """Spawn a subprocess to extract one item. Returns True if timed out."""
    handle = _spawn_item_subprocess(
        config_path=config_path,
        item_id=item_id,
        timeout_seconds=timeout_seconds,
        stall_timeout_seconds=stall_timeout_seconds,
        progress=progress,
        item_index=item_index,
        total_items=total_items,
        item_name=item_name,
        process_registry=process_registry,
        progress_key=None,
    )
    return handle.join()


def _accumulate_subprocess_result(
    connection,
    stats: ExtractionProcessResult,
    queue_id: int,
) -> None:
    row = connection.execute(
        """
        SELECT status, window_count, artifact_count, visual_snippet_count,
               candidate_count, validation_error_count
        FROM extraction_queue
        WHERE id = ?
        """,
        (queue_id,),
    ).fetchone()
    if row is None:
        stats.errors += 1
        return
    status = str(row["status"])
    if status == "processed":
        stats.processed += 1
    elif status == "skipped":
        stats.skipped += 1
    elif status == "error":
        stats.errors += 1
    else:
        stats.errors += 1
        return
    stats.windows += int(row["window_count"] or 0)
    stats.artifacts += int(row["artifact_count"] or 0)
    stats.visual_snippets += int(row["visual_snippet_count"] or 0)
    stats.candidates += int(row["candidate_count"] or 0)
    stats.promotion_ready += _promotion_ready_count(connection, queue_id)


def _promotion_ready_count(connection, queue_id: int) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM extraction_candidates
        WHERE queue_id = ? AND validation_status = 'promotion_ready'
        """,
        (queue_id,),
    ).fetchone()
    return int(row["count"] or 0) if row is not None else 0


def _progress_name(item) -> str:
    raw = str(getattr(item, "archive_id", "") or getattr(item, "id", ""))
    # Strip leading timestamp prefix like "20240101T120000-" if present
    import re
    raw = re.sub(r"^\d{8}T\d{6}-", "", raw)
    return raw[:60]


def _process_item(
    config: AppConfig,
    service: ProjectionService,
    connection,
    *,
    item,
    dry_run: bool,
    progress: dict[str, Any] | None = None,
    start_window: int = 0,
) -> dict[str, int]:
    source_ref = queue_row_to_source_ref(item)
    projection_options = ProjectionOptions(
        max_embedded_images=config.extraction.max_embedded_images,
        min_embedded_image_px=config.extraction.min_embedded_image_px,
    )
    if progress is not None:
        progress.update({
            "phase": "projecting",
            "window": 0,
            "total_windows": 0,
            "artifact": 0,
            "total_artifacts": 0,
        })
    lease = service.open_projection(source_ref, options=projection_options)
    try:
        windows = list(service.iter_text_windows(lease.lease_id))
        artifacts = list(service.iter_artifacts(lease.lease_id))
        if progress is not None:
            progress.update({
                "phase": "vlm" if artifacts else "llm",
                "window": 0,
                "total_windows": len(windows) if windows else 1,
                "artifact": 0,
                "total_artifacts": len(artifacts),
            })
        projection_metadata: dict[str, Any] = {
            **lease.projection_metadata,
            "lease_warnings": list(lease.warnings),
            "source_ref": asdict(lease.source_ref),
        }
        if lease.duplicate_state == "exact_duplicate":
            if not dry_run:
                with connection:
                    mark_finished(
                        connection,
                        queue_id=item.id,
                        status="skipped",
                        duplicate_state=lease.duplicate_state,
                        projection_metadata=projection_metadata,
                        window_count=len(windows),
                        artifact_count=len(artifacts),
                        visual_snippet_count=0,
                        candidate_count=0,
                        validation_error_count=0,
                    )
            return {
                "processed": 0,
                "skipped": 1,
                "windows": len(windows),
                "artifacts": len(artifacts),
                "visual_snippets": 0,
                "candidates": 0,
                "promotion_ready": 0,
            }
        if dry_run:
            return {
                "processed": 1,
                "skipped": 0,
                "windows": len(windows),
                "artifacts": len(artifacts),
                "visual_snippets": 0,
                "candidates": 0,
                "promotion_ready": 0,
            }

        is_resume = start_window > 0

        # On resume: load prior candidate/relation state from DB.
        existing_candidate_count = 0
        existing_intra_relations: list[dict[str, Any]] = []
        if is_resume:
            row = connection.execute(
                "SELECT COUNT(*) FROM extraction_candidates WHERE queue_id = ?", (item.id,)
            ).fetchone()
            existing_candidate_count = row[0] if row else 0
            rel_row = connection.execute(
                "SELECT intra_source_relations_json FROM extraction_queue WHERE id = ?", (item.id,)
            ).fetchone()
            if rel_row and rel_row[0]:
                try:
                    existing_intra_relations = json.loads(rel_row[0])
                except (json.JSONDecodeError, TypeError):
                    pass

        # VLM phase — checkpointed per artifact (issue #35). Snippets already in
        # the DB (from a previous attempt that was killed mid-phase, or from the
        # attempt whose LLM windows we are now resuming) are reused, so each
        # artifact is described at most once across attempts. Artifacts beyond
        # max_vlm_artifacts_per_item get a skipped placeholder so the VLM phase
        # alone can never exceed the item time budget.
        stored_snippets = load_visual_snippets(connection, queue_id=item.id)
        vlm = build_vlm_provider(config)
        vlm_cap = max(0, int(config.extraction.max_vlm_artifacts_per_item))
        # Snippets described by earlier attempts count against the cap too.
        described_so_far = sum(
            1
            for s in stored_snippets.values()
            if not any(w.startswith(_VLM_SKIPPED_WARNING_PREFIX) for w in s.warnings)
        )
        vlm_calls = 0
        vlm_skipped = 0
        visual_snippets: list = []
        for artifact_index, artifact in enumerate(artifacts):
            existing = stored_snippets.get(artifact.artifact_id)
            if existing is not None:
                visual_snippets.append(existing)
                continue
            if progress is not None:
                progress.update({
                    "phase": "vlm",
                    "artifact": artifact_index,
                    "total_artifacts": len(artifacts),
                })
            if described_so_far >= vlm_cap:
                snippet = VisualSnippet(
                    artifact_id=artifact.artifact_id,
                    marker=artifact.marker,
                    source_locator=artifact.source_locator,
                    visual_description="",
                    visible_text="",
                    warnings=[*artifact.warnings, f"{_VLM_SKIPPED_WARNING_PREFIX}max_vlm_artifacts_per_item={vlm_cap}"],
                    model_metadata={"provider": vlm.provider_name, "model": vlm.model_name},
                )
                vlm_skipped += 1
            else:
                snippet = vlm.describe(artifact)
                vlm_calls += 1
                described_so_far += 1
            with connection:
                upsert_visual_snippet(
                    connection,
                    queue_id=item.id,
                    snippet=snippet,
                    model_provider=vlm.provider_name,
                    model_name=vlm.model_name,
                )
            stored_snippets[artifact.artifact_id] = snippet
            visual_snippets.append(snippet)
        if visual_snippets:
            # Totals over the complete set, including snippets checkpointed by
            # earlier attempts, not just this attempt's calls.
            total_skipped = sum(
                1
                for s in visual_snippets
                if any(w.startswith(_VLM_SKIPPED_WARNING_PREFIX) for w in s.warnings)
            )
            projection_metadata["vlm_artifacts_described"] = len(visual_snippets) - total_skipped
            projection_metadata["vlm_artifacts_skipped"] = total_skipped
            projection_metadata["vlm_artifacts_described_this_attempt"] = vlm_calls

        llm = build_llm_provider(config)
        retrieval_context_note_ids = _parse_memory_context_note_ids(windows)

        # Send windows in batches to avoid LLM context overflow.
        # Each window is ~10 000 tokens (ProjectionOptions.window_token_target).
        # Default batch size = 1: one window per LLM call, N candidates out.
        snippet_by_id = {s.artifact_id: s for s in visual_snippets}
        batch_size = config.extraction.max_windows_per_batch
        all_candidates: list[ModelCandidate] = []
        all_intra_relations: list[dict[str, Any]] = []
        total_windows = len(windows) if windows else 1
        window_index = 0
        for batch_windows in _iter_batches(windows, batch_size):
            batch_len = len(batch_windows) if batch_windows else 1
            next_window_index = window_index + batch_len

            if next_window_index <= start_window:
                # Already processed in a prior run; advance index and skip.
                window_index = next_window_index
                continue

            if progress is not None:
                progress["window"] = window_index
                progress["total_windows"] = total_windows
                progress["phase"] = "llm"

            batch_artifact_ids = {
                m.artifact_id for w in batch_windows for m in w.artifact_markers
            }
            batch_snippets = [
                snippet_by_id[aid] for aid in batch_artifact_ids if aid in snippet_by_id
            ]
            packet = make_llm_packet(
                active_source_ref=source_ref_id(item),
                projection_metadata=projection_metadata,
                windows=batch_windows,
                visual_snippets=batch_snippets,
                retrieval_context_note_ids=retrieval_context_note_ids or None,
            )
            raw_output = llm.extract_candidates(packet)
            all_candidates.extend(
                _extract_and_validate_candidates(
                    raw_output=raw_output,
                    active_source_ref=source_ref_id(item),
                    evidence_ids=projection_evidence_ids(packet),
                )
            )
            all_intra_relations.extend(parse_intra_source_relations(raw_output))

            window_index = next_window_index
            with connection:
                update_window_checkpoint(connection, queue_id=item.id, completed_window_count=window_index)

        candidates = all_candidates
        intra_relations = existing_intra_relations + all_intra_relations
        counts = candidate_counts(candidates)
        if progress is not None:
            progress["phase"] = "storing"
        with connection:
            # Visual snippets were already checkpointed one by one above.
            if is_resume:
                append_candidates(
                    connection,
                    queue_id=item.id,
                    candidates=candidates,
                    model_provider=llm.provider_name,
                    model_name=llm.model_name,
                    index_offset=existing_candidate_count,
                )
            else:
                replace_candidates(
                    connection,
                    queue_id=item.id,
                    candidates=candidates,
                    model_provider=llm.provider_name,
                    model_name=llm.model_name,
                )
            mark_finished(
                connection,
                queue_id=item.id,
                status="processed",
                duplicate_state=lease.duplicate_state,
                projection_metadata=projection_metadata,
                window_count=len(windows),
                artifact_count=len(artifacts),
                visual_snippet_count=len(visual_snippets),
                candidate_count=existing_candidate_count + counts["candidate_count"],
                validation_error_count=counts["validation_error_count"],
                intra_source_relations=intra_relations,
            )
        return {
            "processed": 1,
            "skipped": 0,
            "windows": len(windows),
            "artifacts": len(artifacts),
            "visual_snippets": len(visual_snippets),
            "candidates": counts["candidate_count"],
            "promotion_ready": counts["promotion_ready"],
        }
    finally:
        service.close_projection(lease.lease_id)


def _parse_memory_context_note_ids(windows: list[Any]) -> list[str]:
    """Extract note IDs listed under '## Memory Context' in session text windows.

    The mnemonic_context.sh script injects a block like:
        ## Memory Context
        current_hits: concept/foo, concept/bar
        stale_hits: concept/baz

    Parses all values after 'current_hits:', 'stale_hits:', 'contested_hits:' lines.
    Returns deduplicated list of valid note_id strings (must contain '/').
    """
    import re

    note_ids: list[str] = []
    seen: set[str] = set()
    in_memory_section = False
    pattern = re.compile(
        r"^\s*(?:current_hits|stale_hits|contested_hits)\s*:\s*(.+)$", re.IGNORECASE
    )
    for window in windows:
        if isinstance(window, dict):
            text = str(window.get("text") or "")
        else:
            text = str(getattr(window, "text", "") or "")
        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r"^##\s+Memory Context", stripped, re.IGNORECASE):
                in_memory_section = True
                continue
            if in_memory_section and stripped.startswith("## "):
                in_memory_section = False
            if in_memory_section:
                m = pattern.match(line)
                if m:
                    for raw in m.group(1).split(","):
                        nid = raw.strip()
                        if nid and "/" in nid and nid not in seen:
                            seen.add(nid)
                            note_ids.append(nid)
    return note_ids


def _iter_batches(items: list[Any], batch_size: int) -> Iterator[list[Any]]:
    """Yield successive non-overlapping batches.

    Always yields at least one batch (empty list) so callers that must run
    the LLM at least once (e.g. artifact with no text) still execute.
    """
    if not items:
        yield []
        return
    for start in range(0, len(items), batch_size):
        yield items[start: start + batch_size]


def _extract_and_validate_candidates(
    *,
    raw_output: str,
    active_source_ref: str,
    evidence_ids: set[str],
) -> list[ModelCandidate]:
    payloads, bundle_errors = parse_candidate_bundle(raw_output)
    if bundle_errors:
        return [
            ModelCandidate(
                candidate_type="bundle_error",
                title="Invalid candidate bundle",
                payload={"raw_output": raw_output},
                validation_status="invalid",
                validation_errors=bundle_errors,
            )
        ]
    return [
        validate_candidate(payload, active_source_ref=active_source_ref, evidence_ids=evidence_ids)
        for payload in payloads
    ]

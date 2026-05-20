from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
import signal
import subprocess
import sys
from typing import Any, Iterator

from second_brain.config import AppConfig
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import _migrations_dir
from second_brain.projection.pipeline import ProjectionService
from second_brain.projection.pipeline_models import ProjectionOptions

from .models import ExtractionProcessResult, ModelCandidate
from .providers import build_llm_provider, build_vlm_provider, make_llm_packet
from .queue import (
    mark_error,
    mark_finished,
    mark_processing,
    pending_items,
    queue_row_to_source_ref,
    source_ref_id,
    update_window_checkpoint,
)
from .store import append_candidates, candidate_counts, replace_candidates, replace_visual_snippets
from .validation import parse_candidate_bundle, parse_intra_source_relations, projection_evidence_ids, validate_candidate

_log = logging.getLogger(__name__)


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
    max_item_seconds = config.extraction.max_extraction_item_seconds
    try:
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
                mark_processing(connection, item.id)

            timed_out = _run_item_subprocess(
                config_path=str(config.config_path) if config.config_path is not None else None,
                item_id=item.id,
                timeout_seconds=max_item_seconds,
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
                        error=f"timeout: item exceeded {max_item_seconds}s per-item limit",
                    )
            else:
                _accumulate_subprocess_result(connection, stats, item.id)
    finally:
        if progress is not None:
            progress.clear()
        connection.close()
    return stats


def _run_item_subprocess(
    *,
    config_path: str | None,
    item_id: int,
    timeout_seconds: int,
    progress: dict[str, Any] | None,
    item_index: int,
    total_items: int,
    item_name: str,
    process_registry: Any | None = None,
) -> bool:
    """Spawn a subprocess to extract one item. Returns True if timed out."""
    import tempfile
    import threading
    from pathlib import Path

    progress_file = Path(tempfile.gettempdir()) / f"mnemo_progress_{item_id}.json"
    try:
        progress_file.unlink()
    except FileNotFoundError:
        pass

    if progress is not None:
        progress.update({
            "item_id": item_id,
            "item_name": item_name,
            "item_index": item_index,
            "total_items": total_items,
            "phase": "preparing",
            "window": 0,
            "total_windows": 0,
        })

    cmd = [
        sys.executable, "-m", "second_brain.extraction._subprocess_entry",
        "--item-id", str(item_id),
        "--progress-file", str(progress_file),
    ]
    if config_path:
        cmd.extend(["--config", config_path])
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if process_registry is not None:
        process_registry.register(proc, label=f"extraction-item-{item_id}", process_group=True)

    # Poll progress file in background thread and forward into parent's progress dict.
    stop_poll = threading.Event()

    def _poll() -> None:
        while not stop_poll.is_set():
            if progress is not None and progress_file.exists():
                try:
                    data = json.loads(progress_file.read_text())
                    progress.update(data)
                except Exception:
                    pass
            stop_poll.wait(1.5)

    if progress is not None:
        poll_thread = threading.Thread(target=_poll, daemon=True)
        poll_thread.start()
    else:
        poll_thread = None

    try:
        _, stderr = proc.communicate(timeout=timeout_seconds)
        if proc.returncode != 0:
            _log.warning(
                "extraction subprocess for item %s exited %s: %s",
                item_id, proc.returncode, (stderr or b"").decode("utf-8", errors="replace")[:300],
            )
        return False
    except subprocess.TimeoutExpired:
        _log.warning("extraction subprocess for item %s timed out after %ss — killing", item_id, timeout_seconds)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return True
    finally:
        stop_poll.set()
        if poll_thread is not None:
            poll_thread.join(timeout=2.0)
        if process_registry is not None:
            process_registry.unregister(proc)
        try:
            progress_file.unlink()
        except FileNotFoundError:
            pass


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
    lease = service.open_projection(source_ref, options=projection_options)
    try:
        windows = list(service.iter_text_windows(lease.lease_id))
        artifacts = list(service.iter_artifacts(lease.lease_id))
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

        # On resume: skip VLM (snippets already stored) and load prior state from DB.
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
            visual_snippets: list = []
            vlm = None
        else:
            vlm = build_vlm_provider(config)
            visual_snippets = [vlm.describe(artifact) for artifact in artifacts]

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
            if not is_resume:
                replace_visual_snippets(
                    connection,
                    queue_id=item.id,
                    snippets=visual_snippets,
                    model_provider=vlm.provider_name,
                    model_name=vlm.model_name,
                )
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

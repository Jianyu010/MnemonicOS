from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any, Iterator

from second_brain.config import AppConfig
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import _migrations_dir
from second_brain.projection.pipeline import ProjectionService

from .models import ExtractionProcessResult, ModelCandidate
from .providers import build_llm_provider, build_vlm_provider, make_llm_packet
from .queue import (
    mark_error,
    mark_finished,
    mark_processing,
    pending_items,
    queue_row_to_source_ref,
    source_ref_id,
)
from .store import candidate_counts, replace_candidates, replace_visual_snippets
from .validation import parse_candidate_bundle, parse_intra_source_relations, projection_evidence_ids, validate_candidate


def process_extraction_queue(config: AppConfig, *, dry_run: bool = False, limit: int | None = None) -> ExtractionProcessResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    service = ProjectionService(config)
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
        items = pending_items(connection, limit=limit or config.extraction.max_queue_batch)
        for item in items:
            if not dry_run:
                with connection:
                    mark_processing(connection, item.id)
            try:
                item_result = _process_item(config, service, connection, item=item, dry_run=dry_run)
                stats.processed += item_result["processed"]
                stats.skipped += item_result["skipped"]
                stats.windows += item_result["windows"]
                stats.artifacts += item_result["artifacts"]
                stats.visual_snippets += item_result["visual_snippets"]
                stats.candidates += item_result["candidates"]
                stats.promotion_ready += item_result["promotion_ready"]
            except Exception as exc:
                stats.errors += 1
                if not dry_run:
                    error_text = str(exc)
                    if item.attempt_count >= 1:
                        error_text = f"tombstoned: failed after retry (attempt {item.attempt_count + 1}): {error_text}"
                    with connection:
                        mark_error(connection, queue_id=item.id, error=error_text)
    finally:
        connection.close()
    return stats


def _process_item(
    config: AppConfig,
    service: ProjectionService,
    connection,
    *,
    item,
    dry_run: bool,
) -> dict[str, int]:
    source_ref = queue_row_to_source_ref(item)
    lease = service.open_projection(source_ref)
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
        vlm = build_vlm_provider(config)
        llm = build_llm_provider(config)
        visual_snippets = [vlm.describe(artifact) for artifact in artifacts]
        existing_knowledge_context = _existing_knowledge_context(item.revisit_context_json)
        retrieval_context_note_ids = _parse_memory_context_note_ids(windows)

        # Send windows in batches to avoid LLM context overflow.
        # Each window is ~10 000 tokens (ProjectionOptions.window_token_target).
        # Default batch size = 1: one window per LLM call, N candidates out.
        snippet_by_id = {s.artifact_id: s for s in visual_snippets}
        batch_size = config.extraction.max_windows_per_batch
        all_candidates: list[ModelCandidate] = []
        all_intra_relations: list[dict[str, Any]] = []
        for batch_windows in _iter_batches(windows, batch_size):
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
                existing_knowledge_context=existing_knowledge_context,
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

        candidates = all_candidates
        intra_relations = all_intra_relations
        counts = candidate_counts(candidates)
        with connection:
            replace_visual_snippets(
                connection,
                queue_id=item.id,
                snippets=visual_snippets,
                model_provider=vlm.provider_name,
                model_name=vlm.model_name,
            )
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
                candidate_count=counts["candidate_count"],
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


def _existing_knowledge_context(raw_context: str | None) -> str | None:
    if not raw_context:
        return None
    try:
        payload = json.loads(raw_context)
    except json.JSONDecodeError:
        return None
    related = payload.get("related_notes") if isinstance(payload, dict) else None
    if not isinstance(related, list):
        return None
    lines = []
    for item in related:
        if not isinstance(item, dict):
            continue
        note_id = str(item.get("note_id") or "").strip()
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if note_id:
            lines.append(f"[{note_id}] {title}\n{summary}".strip())
    return "\n\n".join(lines) or None


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

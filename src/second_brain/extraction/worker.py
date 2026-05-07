from __future__ import annotations

from dataclasses import asdict
from typing import Any

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
from .validation import parse_candidate_bundle, projection_evidence_ids, validate_candidate


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
                    with connection:
                        mark_error(connection, queue_id=item.id, error=str(exc))
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
        packet = make_llm_packet(
            active_source_ref=source_ref_id(item),
            projection_metadata=projection_metadata,
            windows=windows,
            visual_snippets=visual_snippets,
        )
        candidates = _extract_and_validate_candidates(
            raw_output=llm.extract_candidates(packet),
            active_source_ref=source_ref_id(item),
            evidence_ids=projection_evidence_ids(packet),
        )
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

from __future__ import annotations

import json
from typing import Any

from .models import (
    SCHEMA_VERSION,
    SUPPORTED_CANDIDATE_TYPES,
    SUPPORTED_CONFIDENCE,
    ModelCandidate,
)


def parse_candidate_bundle(raw_output: str) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        return [], [f"invalid_json: {exc.msg}"]
    if not isinstance(payload, dict):
        return [], ["bundle must be a JSON object"]
    if payload.get("schema_version") != SCHEMA_VERSION:
        return [], [f"unsupported_schema_version: {payload.get('schema_version')}"]
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return [], ["candidates must be a list"]
    return [item for item in candidates if isinstance(item, dict)], []


def validate_candidate(
    payload: dict[str, Any],
    *,
    active_source_ref: str,
    evidence_ids: set[str],
) -> ModelCandidate:
    errors: list[str] = []
    candidate_type = str(payload.get("type") or "").strip()
    title = str(payload.get("title") or "").strip()
    for field in ("type", "title", "summary", "body", "confidence", "source_refs", "evidence"):
        value = payload.get(field)
        if value is None or value == "" or value == []:
            errors.append(f"missing_required_field: {field}")
    if candidate_type not in SUPPORTED_CANDIDATE_TYPES:
        errors.append(f"unsupported_type: {candidate_type}")
    confidence = str(payload.get("confidence") or "").strip()
    if confidence not in SUPPORTED_CONFIDENCE:
        errors.append(f"unsupported_confidence: {confidence}")
    source_refs = payload.get("source_refs")
    if not isinstance(source_refs, list) or active_source_ref not in {str(item) for item in source_refs}:
        errors.append("source_refs_missing_active_source")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence_missing")
    else:
        matched = False
        for entry in evidence:
            if not isinstance(entry, dict):
                errors.append("evidence_entry_not_object")
                continue
            evidence_id = str(entry.get("id") or entry.get("source_locator") or entry.get("marker") or "")
            if evidence_id in evidence_ids:
                matched = True
        if not matched:
            errors.append("evidence_not_bound_to_projection")
    validation_status = "promotion_ready" if not errors else "invalid"
    return ModelCandidate(
        candidate_type=candidate_type or "unknown",
        title=title or "Untitled candidate",
        payload=payload,
        validation_status=validation_status,
        validation_errors=errors,
    )


def projection_evidence_ids(packet: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for window in packet.get("text_windows") or []:
        if isinstance(window, dict):
            ids.add(str(window.get("window_id") or ""))
            for span in window.get("source_spans") or []:
                if isinstance(span, dict):
                    ids.add(str(span.get("source_locator") or ""))
            for marker in window.get("artifact_markers") or []:
                if isinstance(marker, dict):
                    ids.add(str(marker.get("marker_text") or ""))
                    ids.add(str(marker.get("artifact_id") or ""))
    for snippet in packet.get("visual_snippets") or []:
        if isinstance(snippet, dict):
            ids.add(str(snippet.get("marker") or ""))
            ids.add(str(snippet.get("artifact_id") or ""))
            ids.add(str(snippet.get("source_locator") or ""))
    return {item for item in ids if item}

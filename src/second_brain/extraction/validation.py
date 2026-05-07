from __future__ import annotations

import json
from typing import Any

from .models import (
    SCHEMA_VERSION,
    SUPPORTED_CANDIDATE_TYPES,
    SUPPORTED_COMPONENT_TYPES,
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
    for field in ("type", "title", "summary", "body", "confidence", "source_refs", "evidence", "components"):
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
        if not _evidence_list_matches(evidence, evidence_ids, errors):
            errors.append("evidence_not_bound_to_projection")
    component_ids = _validate_components(payload.get("components"), evidence_ids, errors)
    _validate_component_links(payload.get("components"), component_ids, errors)
    _validate_temporal_order(payload.get("temporal_order"), component_ids, errors)
    _validate_traces(payload.get("traces"), component_ids, errors)
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


def _evidence_list_matches(evidence: object, evidence_ids: set[str], errors: list[str]) -> bool:
    if not isinstance(evidence, list) or not evidence:
        return False
    matched = False
    for entry in evidence:
        if not isinstance(entry, dict):
            errors.append("evidence_entry_not_object")
            continue
        evidence_id = str(
            entry.get("id")
            or entry.get("window_id")
            or entry.get("source_locator")
            or entry.get("marker")
            or entry.get("artifact_id")
            or ""
        )
        if evidence_id in evidence_ids:
            matched = True
    return matched


def _validate_components(components: object, evidence_ids: set[str], errors: list[str]) -> set[str]:
    component_ids: set[str] = set()
    if not isinstance(components, list) or not components:
        errors.append("components_missing")
        return component_ids
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            errors.append(f"component_not_object: {index}")
            continue
        component_id = str(component.get("component_id") or "").strip()
        if not component_id:
            errors.append(f"component_missing_id: {index}")
        elif component_id in component_ids:
            errors.append(f"component_duplicate_id: {component_id}")
        else:
            component_ids.add(component_id)
        component_type = str(component.get("component_type") or "").strip()
        if component_type not in SUPPORTED_COMPONENT_TYPES:
            errors.append(f"unsupported_component_type: {component_type or '<empty>'}")
        if not str(component.get("text") or "").strip():
            errors.append(f"component_missing_text: {component_id or index}")
        evidence = component.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"component_evidence_missing: {component_id or index}")
        elif not _evidence_list_matches(evidence, evidence_ids, errors):
            errors.append(f"component_evidence_not_bound_to_projection: {component_id or index}")
    return component_ids


def _validate_component_links(components: object, component_ids: set[str], errors: list[str]) -> None:
    if not isinstance(components, list):
        return
    for component in components:
        if not isinstance(component, dict):
            continue
        component_id = str(component.get("component_id") or "<unknown>")
        links = component.get("links")
        if links is None:
            continue
        if not isinstance(links, list):
            errors.append(f"component_links_not_list: {component_id}")
            continue
        for link in links:
            if not isinstance(link, dict):
                errors.append(f"component_link_not_object: {component_id}")
                continue
            target = str(link.get("target") or "").strip()
            if target and target not in component_ids:
                errors.append(f"component_link_dangling: {component_id}->{target}")


def _validate_temporal_order(order: object, component_ids: set[str], errors: list[str]) -> None:
    if order is None:
        return
    if not isinstance(order, list):
        errors.append("temporal_order_not_list")
        return
    for raw_id in order:
        component_id = str(raw_id)
        if component_id not in component_ids:
            errors.append(f"temporal_order_dangling: {component_id}")


def _validate_traces(traces: object, component_ids: set[str], errors: list[str]) -> None:
    if traces is None:
        return
    if not isinstance(traces, list):
        errors.append("traces_not_list")
        return
    trace_ids: set[str] = set()
    for index, trace in enumerate(traces):
        if not isinstance(trace, dict):
            errors.append(f"trace_not_object: {index}")
            continue
        trace_id = str(trace.get("trace_id") or "").strip()
        if not trace_id:
            errors.append(f"trace_missing_id: {index}")
        elif trace_id in trace_ids:
            errors.append(f"trace_duplicate_id: {trace_id}")
        else:
            trace_ids.add(trace_id)
        members = trace.get("component_ids")
        if not isinstance(members, list) or not members:
            errors.append(f"trace_components_missing: {trace_id or index}")
            continue
        for raw_id in members:
            component_id = str(raw_id)
            if component_id not in component_ids:
                errors.append(f"trace_component_dangling: {trace_id or index}->{component_id}")

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

SUPPORTED_ATTRIBUTION_EVIDENCE_TYPES: frozenset[str] = frozenset({
    "paraphrase",
    "semantic_reliance",
    "task_trajectory",
    "correction",
    "contradiction",
    "supersession",
})

# Relation types the LLM may assert between intra-source candidates.
# Mirrors graph.py RELATION_WEIGHTS keys.
SUPPORTED_INTRA_RELATION_TYPES: frozenset[str] = frozenset({
    "owner", "repo", "active_decision", "linked_decision", "linked_procedure",
    "linked_project", "linked_note", "entity", "about", "related_to",
    "part_of", "depends_on", "derived_from", "refines", "supersedes",
    "before", "after", "caused_by", "resolved_by", "evidence_for",
    "similar_to", "contrasts_with", "part_of_chain", "attempts_to_solve",
    "failed_due_to", "supports_solution", "derived_from_attempt",
})


def _strip_code_fence(text: str) -> str:
    """Strip markdown code fences that LLMs commonly wrap JSON in."""
    s = text.strip()
    for prefix in ("```json", "```"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def parse_candidate_bundle(raw_output: str) -> tuple[list[dict[str, Any]], list[str]]:
    cleaned = _strip_code_fence(raw_output)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
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


def parse_intra_source_relations(raw_output: str) -> list[dict[str, Any]]:
    """Parse and validate intra_source_relations from a raw LLM bundle string.

    Invalid entries are dropped silently; the bundle is not rejected.
    Returns a list of clean relation dicts: {source_index, target_index, relation_type[, rationale]}.
    """
    cleaned = _strip_code_fence(raw_output)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []

    candidates = payload.get("candidates")
    candidate_count = len(candidates) if isinstance(candidates, list) else 0

    raw_relations = payload.get("intra_source_relations")
    if not isinstance(raw_relations, list):
        return []

    return validate_intra_source_relations(raw_relations, candidate_count)


def validate_intra_source_relations(
    relations: list[Any],
    candidate_count: int,
) -> list[dict[str, Any]]:
    """Validate a list of intra-source relation dicts.

    Relations use 0-based candidate indices (source_index, target_index) that map
    directly to extraction_candidates.candidate_index for the same queue row.

    Rules (invalid entries dropped, not rejected):
    - source_index and target_index must be non-negative integers < candidate_count.
    - source_index != target_index (no self-loops).
    - relation_type must be in SUPPORTED_INTRA_RELATION_TYPES.
    - Duplicates on (source_index, target_index, relation_type) are dropped.

    When candidate_count == 0 the index range check is skipped (no candidates known).
    Returns only the valid entries.
    """
    valid: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for entry in relations:
        if not isinstance(entry, dict):
            continue
        src_raw = entry.get("source_index")
        tgt_raw = entry.get("target_index")
        rel = str(entry.get("relation_type") or "").strip()
        if src_raw is None or tgt_raw is None or not rel:
            continue
        if isinstance(src_raw, bool) or isinstance(tgt_raw, bool):
            continue
        try:
            src = int(src_raw)
            tgt = int(tgt_raw)
        except (TypeError, ValueError):
            continue
        if src < 0 or tgt < 0:
            continue
        if src == tgt:
            continue
        if candidate_count > 0 and (src >= candidate_count or tgt >= candidate_count):
            continue
        if rel not in SUPPORTED_INTRA_RELATION_TYPES:
            continue
        key = (src, tgt, rel)
        if key in seen:
            continue
        seen.add(key)
        clean: dict[str, Any] = {"source_index": src, "target_index": tgt, "relation_type": rel}
        rationale = str(entry.get("rationale") or "").strip()
        if rationale:
            clean["rationale"] = rationale
        valid.append(clean)
    return valid


def validate_retrieval_attribution(
    raw: object,
    *,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Validate and clean a retrieval_attribution array from a candidate payload.

    Invalid entries are stripped and recorded as warnings. The candidate is never
    rejected solely on attribution errors. Returns the cleaned list (may be empty).
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        warnings.append("retrieval_attribution_not_list")
        return []
    cleaned: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"retrieval_attribution_entry_not_object: {index}")
            continue
        note_id = str(entry.get("note_id") or "").strip()
        evidence_type = str(entry.get("evidence_type") or "").strip()
        if not note_id:
            warnings.append(f"retrieval_attribution_missing_note_id: {index}")
            continue
        if evidence_type not in SUPPORTED_ATTRIBUTION_EVIDENCE_TYPES:
            warnings.append(
                f"retrieval_attribution_unsupported_evidence_type: {evidence_type or '<empty>'} at {index}"
            )
            continue
        key = (note_id, evidence_type)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        confidence_raw = entry.get("confidence")
        if confidence_raw is None:
            confidence = 0.0
        else:
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                warnings.append(f"retrieval_attribution_bad_confidence: {index}")
                confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        cleaned.append({"note_id": note_id, "evidence_type": evidence_type, "confidence": confidence})
    return cleaned


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
    # Validate optional retrieval_attribution. Warnings go into errors list with
    # "retrieval_attribution_warning:" prefix so they are visible but do not block
    # promotion (the final validation_status check is on non-warning errors only).
    attribution_warnings: list[str] = []
    cleaned_attribution = validate_retrieval_attribution(
        payload.get("retrieval_attribution"),
        warnings=attribution_warnings,
    )
    for w in attribution_warnings:
        errors.append(f"retrieval_attribution_warning:{w}")
    # Replace payload attribution with cleaned version (strips invalid entries).
    # Do this on a copy so caller's dict is not mutated unexpectedly.
    if "retrieval_attribution" in payload or cleaned_attribution:
        payload = {**payload, "retrieval_attribution": cleaned_attribution}
    hard_errors = [e for e in errors if not e.startswith("retrieval_attribution_warning:")]
    validation_status = "promotion_ready" if not hard_errors else "invalid"
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

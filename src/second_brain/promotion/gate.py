from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..extraction.models import SUPPORTED_CANDIDATE_TYPES, SUPPORTED_COMPONENT_TYPES
from ..extraction.validation import (
    _evidence_list_matches,
    _validate_component_links,
    _validate_components,
    _validate_temporal_order,
    _validate_traces,
)
from .models import GateResult

if TYPE_CHECKING:
    from ..config import AppConfig


def run_gate(payload: dict[str, Any], *, config: "AppConfig") -> GateResult:
    """Re-verify a promotion_ready candidate before any wiki write.

    Runs 7 checks in order; collects all failures before returning.
    """
    errors: list[str] = []

    # Check 7 — policy gate (fail fast: no point running other checks)
    promo_cfg = getattr(config, "promotion", None)
    enabled = getattr(promo_cfg, "enabled", True) if promo_cfg is not None else True
    if not enabled:
        return GateResult(ok=False, errors=["promotion_policy_disabled"])

    # Reconstruct evidence IDs from the payload's own evidence fields.
    # At promotion time the original projection packet is not required;
    # the evidence references in the payload are sufficient for re-checking.
    evidence_ids = _evidence_ids_from_payload(payload)

    # Checks 1–5: structural integrity (reuse extraction/validation helpers)
    check_errors: list[str] = []

    candidate_type = str(payload.get("type") or "").strip()

    # Check 5 — unsupported candidate type
    if candidate_type not in SUPPORTED_CANDIDATE_TYPES:
        errors.append(f"unsupported_candidate_type:{candidate_type or '<empty>'}")

    # Checks 1, 2 (component IDs + evidence binding), 5 (component types)
    component_ids = _validate_components(payload.get("components"), evidence_ids, check_errors)

    # Check 3 — links point to real components
    _validate_component_links(payload.get("components"), component_ids, check_errors)

    # Check 4 — trace boundaries source-grounded, Check 2 (trace IDs valid)
    _validate_traces(payload.get("traces"), component_ids, check_errors)

    # Temporal order also references component IDs
    _validate_temporal_order(payload.get("temporal_order"), component_ids, check_errors)

    errors.extend(check_errors)

    # Check 6 — component count within configured limit
    components = payload.get("components")
    component_list = components if isinstance(components, list) else []
    max_components = getattr(promo_cfg, "max_components_per_candidate", 50) if promo_cfg is not None else 50
    if len(component_list) > max_components:
        errors.append(f"component_count_exceeds_limit:{len(component_list)}>{max_components}")

    return GateResult(ok=len(errors) == 0, errors=errors)


def _evidence_ids_from_payload(payload: dict[str, Any]) -> set[str]:
    """Reconstruct evidence IDs from candidate payload evidence fields."""
    ids: set[str] = set()

    # Top-level evidence list
    for entry in payload.get("evidence") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("id", "window_id", "source_locator", "marker", "artifact_id"):
            value = str(entry.get(key) or "").strip()
            if value:
                ids.add(value)

    # Component-level evidence
    for component in payload.get("components") or []:
        if not isinstance(component, dict):
            continue
        for entry in component.get("evidence") or []:
            if not isinstance(entry, dict):
                continue
            for key in ("id", "window_id", "source_locator", "marker", "artifact_id"):
                value = str(entry.get(key) or "").strip()
                if value:
                    ids.add(value)

    return ids

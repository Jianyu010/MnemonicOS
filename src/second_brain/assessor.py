from __future__ import annotations

from .models import AssessmentDirective, MemoryPacket, RetrievalAssessment, RetrieveHit, RuntimeMemory
from .trust import query_temporality


DEFAULT_VERIFICATION_ACTION = "none"
CONTENT_MARKERS = frozenset({"WHEN", "DO", "Steps", "Cause", "Fix", "Avoid", "->", "NOT"})


def assess_hits(
    hits: list[RetrieveHit],
    query: str,
    *,
    temporality_override: str | None = None,
) -> RetrievalAssessment:
    temporality = temporality_override or query_temporality(query)
    return _assess_hit_list(hits, temporality=temporality)


def assess_payload(
    payload: dict[str, object],
    query: str,
    *,
    temporality_override: str | None = None,
) -> RetrievalAssessment:
    temporality = temporality_override or str(payload.get("temporality") or "") or query_temporality(query)
    hits: list[RetrieveHit] = []
    raw_hits = payload.get("hits", [])
    if isinstance(raw_hits, list):
        for raw_hit in raw_hits:
            if not isinstance(raw_hit, dict):
                continue
            hits.append(
                RetrieveHit(
                    id=str(raw_hit.get("id")),
                    title=str(raw_hit.get("title") or ""),
                    type=str(raw_hit.get("type") or ""),
                    status=str(raw_hit["status"]) if raw_hit.get("status") is not None else None,
                    chain_id=str(raw_hit["chain_id"]) if raw_hit.get("chain_id") is not None else None,
                    freshness_state=str(raw_hit["freshness_state"]) if raw_hit.get("freshness_state") is not None else None,
                    lifecycle_state=str(raw_hit["lifecycle_state"]) if raw_hit.get("lifecycle_state") is not None else None,
                    body_path=str(raw_hit.get("body_path") or ""),
                    summary=str(raw_hit.get("summary") or ""),
                    channels=[str(channel) for channel in raw_hit.get("channels", [])],
                    raw_score=float(raw_hit["raw_score"]) if raw_hit.get("raw_score") is not None else None,
                    final_score=float(raw_hit.get("final_score") or 0.0),
                    excerpt=str(raw_hit.get("excerpt") or ""),
                    relation_types=[str(value) for value in raw_hit.get("relation_types", [])],
                )
            )
    return _assess_hit_list(hits, temporality=temporality)


def assess_packet(
    packet: MemoryPacket,
    *,
    query: str,
    task_kind: str,
    temporality: str | None = None,
    query_type: str | None = None,
) -> AssessmentDirective:
    try:
        retrieval_state, trust_reasons = _retrieval_state_for_packet(packet, temporality=temporality)
        completion_state, completion_reasons = _completion_state_for_packet(
            packet,
            task_kind=task_kind,
            query=query,
            query_type=query_type,
        )
        threshold, threshold_reasons = _fresh_pipeline_threshold(packet, retrieval_state, completion_state)
        selected, deferred = _select_branches(packet, retrieval_state, completion_state)
        reuse_mode, recommendation, verification_action = _reuse_policy(retrieval_state, completion_state, threshold)
        emission_profile = _emission_profile(retrieval_state, completion_state, selected)
        hydration_policy = _hydration_policy(emission_profile)
        followup = _followup_policy(threshold, deferred)
        reasons = _dedupe_ordered([*trust_reasons, *completion_reasons, *threshold_reasons])
        return AssessmentDirective(
            retrieval_state=retrieval_state,
            completion_state=completion_state,
            reuse_mode=reuse_mode,
            recommendation=recommendation,
            verification_action=verification_action,
            emission_profile=emission_profile,
            hydration_policy=hydration_policy,
            selected_branch_ids=selected,
            deferred_branch_ids=deferred,
            missing_roles=[str(value) for value in packet.assembly_notes.get("missing_functional_roles", [])],
            followup_query_policy=followup,
            fresh_pipeline_threshold=threshold,
            assessment_reasons=reasons,
        )
    except Exception as exc:  # pragma: no cover - defensive no-raise path
        return AssessmentDirective(
            retrieval_state="insufficient_no_hit",
            completion_state="fragmentary",
            reuse_mode="fresh_research_required",
            recommendation="research_fresh",
            verification_action="check_recent_sources",
            emission_profile="no_memory",
            hydration_policy="summaries_only",
            fresh_pipeline_threshold="no_anchor",
            assessment_reasons=[f"assessment_degraded:{type(exc).__name__}"],
        )


def verification_action(assessment: RetrievalAssessment) -> str:
    if assessment.state == "sufficient_historical":
        return "treat_as_historical_only"
    if assessment.recommendation == "reuse":
        return DEFAULT_VERIFICATION_ACTION
    if assessment.state == "partial":
        return "verify_current_truth"
    if assessment.state in {"insufficient_stale", "insufficient_contested"}:
        return "check_recent_sources"
    return "check_recent_sources"


def _hit_has_content_quality(hit: RetrieveHit) -> bool:
    title = hit.title.strip()
    summary = hit.summary.strip()
    excerpt = hit.excerpt.strip()
    if summary and summary != title:
        return True
    excerpt_words = excerpt.split()
    return len(excerpt_words) >= 20 or any(marker in excerpt for marker in CONTENT_MARKERS)


def _memory_has_content_quality(memory: RuntimeMemory) -> bool:
    title = memory.title.strip()
    summary = memory.summary.strip()
    if summary and summary != title:
        return True
    return any(marker in summary for marker in CONTENT_MARKERS)


def _assess_hit_list(hits: list[RetrieveHit], *, temporality: str) -> RetrievalAssessment:
    note_hits = [hit for hit in hits if hit.type != "archive_chunk"]
    if not note_hits:
        return RetrievalAssessment("insufficient_no_hit", "research_fresh", [], [], [], [], [])

    stale_ids: list[str] = []
    contested_ids: list[str] = []
    current_ids: list[str] = []
    partial_ids: list[str] = []
    hit_ids = [hit.id for hit in note_hits]

    for hit in note_hits:
        freshness = hit.freshness_state or "fresh"
        if freshness == "contested":
            contested_ids.append(hit.id)
        elif freshness == "stale":
            stale_ids.append(hit.id)
        elif freshness == "suspect":
            partial_ids.append(hit.id)
        else:
            current_ids.append(hit.id)

    if temporality == "historical":
        return RetrievalAssessment(
            "sufficient_historical",
            "reuse_with_verification",
            hit_ids,
            stale_ids,
            contested_ids,
            current_ids,
            partial_ids,
        )

    quality_failed = [hit.id for hit in note_hits if not _hit_has_content_quality(hit)]
    if contested_ids and not current_ids:
        state, recommendation = "insufficient_contested", "research_fresh"
    elif stale_ids and len(stale_ids) == len(note_hits):
        state, recommendation = "insufficient_stale", "research_fresh"
    elif partial_ids or quality_failed:
        state, recommendation = "partial", "reuse_with_verification"
    elif current_ids:
        state, recommendation = "sufficient_current", "reuse"
    elif contested_ids:
        state, recommendation = "insufficient_contested", "research_fresh"
    else:
        state, recommendation = "insufficient_stale", "research_fresh"

    return RetrievalAssessment(
        state,
        recommendation,
        hit_ids,
        stale_ids,
        contested_ids,
        current_ids,
        _dedupe_ordered([*partial_ids, *quality_failed]),
    )


def _retrieval_state_for_packet(packet: MemoryPacket, *, temporality: str | None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    primary = packet.primary_memories
    if packet.contradictions:
        return "insufficient_contested", ["primary_contradiction"]
    if not primary and not packet.archive_context:
        suppressed = int(packet.assembly_notes.get("suppressed_by_gate_count", 0))
        reason = "relevance_gate_suppressed_all" if suppressed > 0 else "no_primary_or_archive"
        return "insufficient_no_hit", [reason]
    if not primary and packet.archive_context:
        return "partial", ["archive_only"]
    if temporality == "historical":
        return "sufficient_historical", ["historical_query"]

    freshness = [(item.freshness_state or "fresh") for item in primary]
    if all(value == "stale" for value in freshness):
        return "insufficient_stale", ["all_primary_stale"]
    quality_failed = [item.id for item in primary if not _memory_has_content_quality(item)]
    warning_ids = {str(value) for value in packet.assembly_notes.get("warning_item_ids", [])}
    if any(value in {"contested"} for value in freshness):
        return "insufficient_contested", ["primary_contested"]
    if any(value in {"suspect"} for value in freshness):
        reasons.append("primary_suspect")
    if quality_failed:
        reasons.append("primary_quality_failed")
    if warning_ids:
        reasons.append("primary_warning")
    if reasons:
        return "partial", reasons
    return "sufficient_current", ["all_primary_current"]


def _completion_state_for_packet(
    packet: MemoryPacket,
    *,
    task_kind: str,
    query: str,
    query_type: str | None,
) -> tuple[str, list[str]]:
    if not packet.primary_memories and not packet.archive_context:
        return "fragmentary", ["no_memory_path"]
    reservoir_count = int(packet.assembly_notes.get("assessment_reservoir_branch_count") or len(packet.assessment_reservoir))
    deferred_count = len(packet.assembly_notes.get("deferred_branch_ids", []))
    if reservoir_count > 1 or deferred_count > 0:
        return "overabundant", ["reservoir_has_deferred_branches"]

    roles = {str(item.get("assembly_role") or "") for item in packet.functional_path}
    missing_roles = [str(value) for value in packet.assembly_notes.get("missing_functional_roles", [])]
    intent = _intent(task_kind, query, query_type)

    if intent == "factual":
        return ("complete_path", ["factual_primary_present"]) if packet.primary_memories else ("fragmentary", ["factual_no_primary"])
    if not packet.functional_path:
        primary_roles = {item.assembly_role or _role_from_memory(item) for item in packet.primary_memories}
        if primary_roles & {"current_reusable_procedure", "successful_resolution"}:
            return "success_only", ["primary_success_without_path"]
        return "fragmentary", ["no_functional_path"]
    if missing_roles:
        return "partial_chain", ["missing_functional_roles"]
    if roles & {"successful_resolution", "current_reusable_procedure"}:
        if roles & {"failed_attempt", "diagnosis", "decision_pivot", "caution", "context"}:
            return "complete_path", ["required_roles_present"]
        return "success_only", ["success_without_context"]
    if len(packet.functional_path) >= 2:
        return "partial_chain", ["linked_path_without_terminal_success"]
    return "fragmentary", ["isolated_path_item"]


def _fresh_pipeline_threshold(packet: MemoryPacket, retrieval_state: str, completion_state: str) -> tuple[str, list[str]]:
    if retrieval_state == "insufficient_contested":
        return "contradiction_blocks_use", ["contradiction_blocks_use"]
    if packet.assembly_notes.get("archive_only") and completion_state != "complete_path":
        return "archive_only_actionable", ["archive_only_actionable"]
    if packet.assembly_notes.get("budget_limited") and packet.assembly_notes.get("primary_empty"):
        return "budget_trimmed_primary", ["budget_trimmed_primary"]
    if packet.assembly_notes.get("missing_functional_roles") and completion_state in {"partial_chain", "fragmentary"}:
        return "missing_required_role", ["missing_required_role"]
    if retrieval_state == "insufficient_no_hit":
        return "no_anchor", ["no_anchor"]
    return "none", []


def _select_branches(packet: MemoryPacket, retrieval_state: str, completion_state: str) -> tuple[list[str], list[str]]:
    if retrieval_state.startswith("insufficient") or completion_state == "fragmentary":
        return [], [str(branch.get("branch_id")) for branch in packet.assessment_reservoir if branch.get("branch_id")]
    default_branch = str(packet.assembly_notes.get("default_branch_id") or "")
    selected = [default_branch] if default_branch else []
    if not selected and packet.assessment_reservoir:
        selected = [str(packet.assessment_reservoir[0].get("branch_id"))]
    deferred = [
        str(branch.get("branch_id"))
        for branch in packet.assessment_reservoir
        if branch.get("branch_id") and str(branch.get("branch_id")) not in selected
    ]
    return selected, deferred


def _reuse_policy(retrieval_state: str, completion_state: str, threshold: str) -> tuple[str, str, str]:
    if threshold != "none" or retrieval_state.startswith("insufficient"):
        return "fresh_research_required", "research_fresh", "check_recent_sources"
    if retrieval_state == "sufficient_historical":
        return "reuse_with_verification", "reuse_with_verification", "treat_as_historical_only"
    if retrieval_state == "partial":
        return "scaffold_only" if completion_state in {"fragmentary", "partial_chain"} else "reuse_with_verification", "reuse_with_verification", "verify_current_truth"
    if completion_state in {"partial_chain", "fragmentary"}:
        return "diagnose_on_failure", "reuse_with_verification", "verify_current_truth"
    return "direct_reuse", "reuse", DEFAULT_VERIFICATION_ACTION


def _emission_profile(retrieval_state: str, completion_state: str, selected: list[str]) -> str:
    if retrieval_state == "insufficient_no_hit":
        return "no_memory"
    if retrieval_state.startswith("insufficient") or not selected:
        return "warning_only"
    if completion_state == "overabundant":
        return "success_plus_major_failures"
    if completion_state in {"complete_path", "success_only"}:
        return "concise_success_path"
    if completion_state == "partial_chain":
        return "success_plus_major_failures"
    return "warning_only"


def _hydration_policy(emission_profile: str) -> str:
    if emission_profile == "concise_success_path":
        return "hydrate_primary_only"
    if emission_profile == "success_plus_major_failures":
        return "hydrate_primary_and_failure_branches"
    if emission_profile == "full_chain":
        return "hydrate_full_functional_path"
    return "summaries_only"


def _followup_policy(threshold: str, deferred: list[str]) -> str:
    if threshold in {"missing_required_role", "new_error_signature", "contradiction_blocks_use", "no_anchor"}:
        return "immediate_focused_requery"
    if deferred:
        return "on_first_failure"
    return "none"


def _intent(task_kind: str, query: str, query_type: str | None) -> str:
    text = " ".join([task_kind, query, query_type or ""]).casefold()
    if any(token in text for token in {"debug", "repair", "recover", "fix", "failure", "failed", "error"}):
        return "debugging"
    if any(token in text for token in {"history", "historical", "previous", "previously"}):
        return "historical"
    if any(token in text for token in {"compare", "choice", "choose", "alternative"}):
        return "comparison"
    if task_kind == "coding" or query_type == "how_to" or any(token in text for token in {"implement", "deploy", "procedure"}):
        return "implementation"
    return "factual"


def _role_from_memory(memory: RuntimeMemory) -> str:
    if memory.type == "procedure":
        return "current_reusable_procedure"
    if memory.type in {"incident", "journal"}:
        return "failed_attempt"
    if memory.type == "decision":
        return "decision_pivot"
    return "context"


def _dedupe_ordered(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped

from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any

from .models import AssessmentDirective, MemoryPacket, RuntimeMemory


DEFAULT_MAX_FOCUSED_RETRIES = 1


def packet_stable_ids(packet: MemoryPacket) -> list[str]:
    ids: list[str] = []
    for item in _all_packet_memories(packet):
        if item.id not in ids:
            ids.append(item.id)
    for branch in packet.assessment_reservoir:
        for item_id in branch.get("item_ids") or []:
            if isinstance(item_id, str) and item_id not in ids:
                ids.append(item_id)
    return ids


def render_emission(
    result: Any,
    *,
    original_task_text: str,
    task_kind: str,
    temporality: str,
    auto_retry_allowed: bool = False,
    max_focused_retries: int = DEFAULT_MAX_FOCUSED_RETRIES,
    retry_attempt: int = 0,
    cutoff_reason: str | None = None,
    previous_ids: list[str] | None = None,
    previous_queries: list[str] | None = None,
) -> dict[str, Any]:
    directive = result.assessment_directive
    packet: MemoryPacket = result.memory_packet
    followup_query = _build_followup_query(
        directive,
        packet,
        original_task_text=original_task_text,
        previous_queries=previous_queries or [],
    )
    retry_control = {
        "auto_retry_allowed": bool(auto_retry_allowed),
        "max_focused_retries": max(0, int(max_focused_retries)),
        "retry_attempt": max(0, int(retry_attempt)),
        "stop_if_no_new_ids": True,
        "stop_if_same_threshold": True,
        "stop_if_same_missing_roles": True,
        "cutoff_reason": cutoff_reason,
    }
    packet_ids = packet_stable_ids(packet)
    previous_set = set(previous_ids or [])
    new_ids = [item_id for item_id in packet_ids if item_id not in previous_set]
    return {
        "protocol": getattr(result, "protocol", "mnemos-runtime"),
        "version": getattr(result, "version", "0.1"),
        "operation": "emit_memory_context",
        "task_id": result.task_id,
        "session_id": result.session_id,
        "retrieval_state": _directive_value(directive, "retrieval_state", result.retrieval_state),
        "completion_state": _directive_value(directive, "completion_state", ""),
        "reuse_mode": _directive_value(directive, "reuse_mode", ""),
        "recommendation": _directive_value(directive, "recommendation", result.recommendation),
        "verification_action": _directive_value(directive, "verification_action", result.verification_action),
        "emission_profile": _directive_value(directive, "emission_profile", ""),
        "hydration_policy": _directive_value(directive, "hydration_policy", ""),
        "fresh_pipeline_threshold": _directive_value(directive, "fresh_pipeline_threshold", "none"),
        "followup_query_policy": _directive_value(directive, "followup_query_policy", "none"),
        "assessment_directive": directive.to_dict() if directive else None,
        "selected_packet": _selected_packet_view(packet, directive),
        "archive_context": [asdict(item) for item in packet.archive_context],
        "warnings": list(packet.warnings),
        "contradictions": list(packet.contradictions),
        "missing_roles": list(getattr(directive, "missing_roles", []) if directive else []),
        "deferred_context": _deferred_context(packet, directive),
        "followup_query": followup_query,
        "retry_control": retry_control,
        "stable_ids": packet_ids,
        "new_ids": new_ids,
        "provenance": {
            "query_id": result.query_id,
            "task_kind": task_kind,
            "temporality": temporality,
            "provenance_summary": list(getattr(result, "provenance_summary", [])),
        },
        "debug": {
            "query_id": result.query_id,
            "query_type": getattr(result, "query_type", None),
            "classifier_confidence": getattr(result, "classifier_confidence", None),
            "persona": getattr(result, "persona", None),
            "back_feed_hook": getattr(result, "back_feed_hook", None),
        },
    }


def render_single_markdown(emission: dict[str, Any]) -> str:
    lines: list[str] = [
        "<!-- MnemonicOS orchestrator context — do not edit -->",
        "## Memory Context",
        "",
        f"- retrieval_state: `{emission.get('retrieval_state')}`",
        f"- recommendation: `{emission.get('recommendation')}`",
        f"- verification_action: `{emission.get('verification_action')}`",
    ]
    if emission.get("completion_state"):
        lines.append(f"- completion_state: `{emission.get('completion_state')}`")
    if emission.get("emission_profile"):
        lines.append(f"- emission_profile: `{emission.get('emission_profile')}`")
    lines += [f"- task_id: `{emission.get('task_id')}`", ""]

    if emission.get("warnings"):
        lines += ["### Warnings", ""]
        for warning in emission["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    selected = emission.get("selected_packet") or {}
    _append_memory_lines(lines, "Primary Memory", selected.get("primary_memories") or [])
    _append_memory_lines(lines, "Same Episode", selected.get("same_episode_context") or [])
    _append_memory_lines(lines, "Related by Topic", selected.get("related_by_topic") or [])
    _append_memory_lines(lines, "Related by Timeline", selected.get("related_by_timeline") or [])
    _append_memory_lines(lines, "Related by Similarity", selected.get("related_by_similarity") or [])
    _append_memory_lines(lines, "Related by Contrast", selected.get("related_by_contrast") or [])
    _append_memory_lines(lines, "Archive Evidence", emission.get("archive_context") or [])

    if emission.get("contradictions"):
        lines += ["### Contradictions", ""]
        for contradiction in emission["contradictions"]:
            item_ids = contradiction.get("item_ids") or contradiction.get("ids") or []
            severity = contradiction.get("severity") or "unknown"
            lines.append(f"- severity={severity}; item_ids={', '.join(map(str, item_ids))}")
        lines.append("")

    if emission.get("missing_roles"):
        lines += ["### Missing Required Role", ""]
        for role in emission["missing_roles"]:
            lines.append(f"- {role}")
        lines.append("")

    followup = emission.get("followup_query")
    if followup:
        heading = "Suggested Focused MnemonicOS Query"
        lines += [f"### {heading}", ""]
        if followup.get("policy") == "on_first_failure":
            lines.append("If this path fails, ask:")
        else:
            lines.append("Memory is partial. Ask a focused follow-up:")
        lines.append(f"\"{followup.get('suggested_query', '')}\"")
        lines.append("")

    cutoff = (emission.get("retry_control") or {}).get("cutoff_reason")
    if cutoff:
        lines += [
            "### Retry Cutoff",
            "",
            "Memory remains partial after focused retry.",
            f"Cutoff: {cutoff}",
            "Do not keep querying automatically.",
            "",
        ]

    if not _has_visible_memory(emission):
        lines += ["### Relevant Notes", "", "- No relevant MnemonicOS notes found.", ""]

    lines.append("<!-- end MnemonicOS orchestrator context -->")
    return "\n".join(lines)


def render_composed_markdown(
    final_emission: dict[str, Any],
    *,
    retrieval_passes: list[dict[str, Any]],
    final_pass_index: int,
    auto_retry_summary: dict[str, Any],
) -> str:
    markdown = render_single_markdown(final_emission)
    if len(retrieval_passes) <= 1:
        return markdown
    lines = markdown.splitlines()
    if lines and lines[-1].startswith("<!-- end MnemonicOS"):
        end_marker = lines.pop()
    else:
        end_marker = "<!-- end MnemonicOS orchestrator context -->"
    lines += ["", "### Retry Trace", ""]
    for retrieval_pass in retrieval_passes:
        emission = retrieval_pass.get("emission") or {}
        pass_index = retrieval_pass.get("pass_index")
        query_id = retrieval_pass.get("query_id")
        if pass_index == 1 and retrieval_pass.get("triggered_followup_query"):
            followup = retrieval_pass["triggered_followup_query"]
            roles = ", ".join(followup.get("missing_roles") or [])
            lines.append(f"- Pass {pass_index} query_id={query_id} emitted focused query for missing role {roles}.")
        else:
            new_ids = retrieval_pass.get("new_ids") or emission.get("new_ids") or []
            cutoff = retrieval_pass.get("cutoff_reason")
            if new_ids:
                lines.append(f"- Pass {pass_index} query_id={query_id} found {len(new_ids)} new memory: {', '.join(new_ids)}.")
            elif cutoff:
                lines.append(f"- Pass {pass_index} query_id={query_id} found no new memory. Cutoff: {cutoff}.")
            else:
                lines.append(f"- Pass {pass_index} query_id={query_id} completed.")
    if auto_retry_summary.get("cutoff_reason"):
        lines.append(f"- Final pass index: {final_pass_index}; cutoff={auto_retry_summary['cutoff_reason']}.")
    else:
        lines.append(f"- Final pass index: {final_pass_index}.")
    lines.append(end_marker)
    return "\n".join(lines)


def should_auto_retry(emission: dict[str, Any]) -> bool:
    retry_control = emission.get("retry_control") or {}
    followup = emission.get("followup_query")
    return bool(
        retry_control.get("auto_retry_allowed")
        and followup
        and followup.get("policy") == "immediate_focused_requery"
        and retry_control.get("retry_attempt", 0) < retry_control.get("max_focused_retries", 0)
        and not retry_control.get("cutoff_reason")
    )


def retry_cutoff_reason(
    first_emission: dict[str, Any],
    second_emission: dict[str, Any],
    *,
    previous_queries: list[str],
    max_focused_retries: int,
    retry_attempt: int,
) -> str | None:
    second_ids = second_emission.get("stable_ids") or []
    new_ids = [item_id for item_id in second_ids if item_id not in set(first_emission.get("stable_ids") or [])]
    if not second_ids or not new_ids:
        return "no_new_ids"
    followup = second_emission.get("followup_query") or {}
    if followup.get("suggested_query") and followup.get("suggested_query") in previous_queries:
        return "same_suggested_query"
    if (
        second_emission.get("fresh_pipeline_threshold") == first_emission.get("fresh_pipeline_threshold")
        and second_emission.get("missing_roles") == first_emission.get("missing_roles")
        and second_emission.get("followup_query_policy") == "immediate_focused_requery"
    ):
        return "same_threshold_and_roles"
    if second_emission.get("retrieval_state") in {"insufficient_no_hit", "insufficient_stale", "insufficient_contested"}:
        return "terminal_insufficient_state"
    if retry_attempt >= max_focused_retries and second_emission.get("followup_query_policy") == "immediate_focused_requery":
        return "retry_budget_exhausted"
    return None


def _directive_value(directive: AssessmentDirective | None, field_name: str, fallback: Any) -> Any:
    if directive is None:
        return fallback
    value = getattr(directive, field_name, None)
    return fallback if value is None else value


def _all_packet_memories(packet: MemoryPacket) -> list[RuntimeMemory]:
    return [
        *packet.primary_memories,
        *packet.same_episode_context,
        *packet.related_by_topic,
        *packet.related_by_timeline,
        *packet.related_by_similarity,
        *packet.related_by_contrast,
        *packet.archive_context,
    ]


def _selected_packet_view(packet: MemoryPacket, directive: AssessmentDirective | None) -> dict[str, Any]:
    selected_branch_ids = set(directive.selected_branch_ids if directive else [])

    def include(item: RuntimeMemory) -> bool:
        if not selected_branch_ids:
            return True
        return item.branch_id is None or item.branch_id in selected_branch_ids

    return {
        "primary_memories": [asdict(item) for item in packet.primary_memories if include(item)],
        "same_episode_context": [asdict(item) for item in packet.same_episode_context if include(item)],
        "related_by_topic": [asdict(item) for item in packet.related_by_topic if include(item)],
        "related_by_timeline": [asdict(item) for item in packet.related_by_timeline if include(item)],
        "related_by_similarity": [asdict(item) for item in packet.related_by_similarity if include(item)],
        "related_by_contrast": [asdict(item) for item in packet.related_by_contrast if include(item)],
        "functional_path": list(packet.functional_path),
    }


def _deferred_context(packet: MemoryPacket, directive: AssessmentDirective | None) -> dict[str, Any]:
    deferred_ids = list(directive.deferred_branch_ids if directive else [])
    branches = [
        branch for branch in packet.assessment_reservoir
        if isinstance(branch, dict) and branch.get("branch_id") in deferred_ids
    ]
    return {"deferred_branch_ids": deferred_ids, "branches": branches}


def _build_followup_query(
    directive: AssessmentDirective | None,
    packet: MemoryPacket,
    *,
    original_task_text: str,
    previous_queries: list[str],
) -> dict[str, Any] | None:
    if directive is None or directive.followup_query_policy == "none":
        return None
    missing_roles = list(directive.missing_roles)
    note_ids = _anchor_note_ids(packet, directive)
    chain_id = _first_value([item.chain_id for item in _all_packet_memories(packet)])
    branch_id = _first_branch_id(packet, directive)
    relation_target = _relation_target(packet, note_ids)
    error_signature = _error_signature(original_task_text)
    anchors = {
        "original_intent": _compact_intent(original_task_text),
        "note_ids": note_ids,
        "chain_id": chain_id,
        "branch_id": branch_id,
        "error_signature": error_signature,
        "relation_target": relation_target,
    }
    terms = [
        anchors["original_intent"],
        " ".join(missing_roles),
        f"chain_id:{chain_id}" if chain_id else "",
        relation_target or "",
    ]
    suggested_query = " ".join(term for term in terms if term).strip()
    if not suggested_query:
        suggested_query = original_task_text.strip()
    return {
        "policy": directive.followup_query_policy,
        "threshold": directive.fresh_pipeline_threshold,
        "missing_roles": missing_roles,
        "anchors": anchors,
        "suggested_query": suggested_query,
        "repeats_previous_query": suggested_query in previous_queries,
    }


def _anchor_note_ids(packet: MemoryPacket, directive: AssessmentDirective) -> list[str]:
    selected = set(directive.selected_branch_ids)
    ids: list[str] = []
    for item in _all_packet_memories(packet):
        if selected and item.branch_id and item.branch_id not in selected and item.branch_id not in directive.deferred_branch_ids:
            continue
        if item.id not in ids:
            ids.append(item.id)
    return ids[:8]


def _first_branch_id(packet: MemoryPacket, directive: AssessmentDirective) -> str | None:
    for branch_id in [*directive.selected_branch_ids, *directive.deferred_branch_ids]:
        if branch_id:
            return branch_id
    for item in _all_packet_memories(packet):
        if item.branch_id:
            return item.branch_id
    return None


def _relation_target(packet: MemoryPacket, note_ids: list[str]) -> str | None:
    for item_id in note_ids:
        if item_id.startswith(("procedure/", "decision/", "incident/", "concept/")):
            return item_id
    for item in _all_packet_memories(packet):
        if item.type == "procedure":
            return item.id
    return note_ids[0] if note_ids else None


def _first_value(values: list[str | None]) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _compact_intent(text: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_/-]+", text.lower())
    stop = {"the", "a", "an", "to", "for", "with", "how", "do", "we", "please", "can", "you"}
    useful = [token for token in tokens if token not in stop]
    return " ".join(useful[:8])


def _error_signature(text: str) -> str | None:
    patterns = [
        r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception):\s*[^\n]+)",
        r"(error:\s*[^\n]+)",
        r"(failed:\s*[^\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)[:160]
    return None


def _append_memory_lines(lines: list[str], heading: str, memories: list[dict[str, Any]]) -> None:
    if not memories:
        return
    lines.extend([f"### {heading}", ""])
    for memory in memories:
        flag = ""
        freshness = memory.get("freshness_state")
        lifecycle = memory.get("lifecycle_state")
        if freshness in ("stale", "suspect", "contested"):
            flag = f" [{freshness}]"
        elif lifecycle in ("contested", "archived"):
            flag = f" [{lifecycle}]"
        title = memory.get("title") or memory.get("id") or "Untitled"
        memory_type = memory.get("type") or "memory"
        lines.append(f"**{title}** ({memory_type}){flag}")
        if memory.get("summary"):
            lines.append(f"  {str(memory['summary'])[:200]}")
        lines.append("")


def _has_visible_memory(emission: dict[str, Any]) -> bool:
    selected = emission.get("selected_packet") or {}
    return any(
        selected.get(slot)
        for slot in (
            "primary_memories",
            "same_episode_context",
            "related_by_topic",
            "related_by_timeline",
            "related_by_similarity",
            "related_by_contrast",
        )
    ) or bool(emission.get("archive_context"))

"""
Shared orchestrator facade over MnemonicOS runtime operations.

Three public operations:
  begin_task   — resolve context before an LLM call
  mark_failure — re-resolve after the first meaningful failure, preserving task_id
  finish_task  — record final outcome and compute the writeback decision

Transport-agnostic: called identically by Python imports, CLI commands, and the HTTP server.
The underlying retrieval, outcome logging, and writeback decisioning remain in runtime.py.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import AppConfig
from .emission import (
    DEFAULT_MAX_FOCUSED_RETRIES,
    render_composed_markdown,
    render_emission,
    render_single_markdown,
    retry_cutoff_reason,
    should_auto_retry,
)
from .models import (
    DecideWritebackRequest,
    MemoryPacket,
    RecordOutcomeRequest,
    ResolveTaskContextRequest,
)
from .runtime import (
    decide_writeback_runtime,
    record_outcome,
    resolve_task_context,
)


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class OrchestratorTaskRequest:
    """Input to begin_task.  session_id and task_id are auto-generated when absent."""
    bridge_id: str
    task_text: str
    session_id: str = field(default_factory=lambda: f"session-{uuid.uuid4().hex[:12]}")
    task_id: str = field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    task_kind: str = ""           # inferred from task_text when empty
    temporality: str | None = None   # inferred from task_kind when None
    token_budget: int | None = None  # defaulted by task_kind when None
    workspace: str | None = None
    project: str | None = None
    repo: str | None = None
    tags: list[str] = field(default_factory=list)
    auto_retry: bool = False
    max_focused_retries: int = DEFAULT_MAX_FOCUSED_RETRIES


@dataclass(slots=True)
class OrchestratorContextResult:
    """Output of begin_task and mark_failure."""
    task_id: str
    session_id: str
    retrieval_state: str
    recommendation: str
    verification_action: str
    memory_packet: MemoryPacket
    query_id: int
    context_markdown: str
    task_kind: str
    temporality: str
    emission: dict[str, Any] = field(default_factory=dict)
    retrieval_passes: list[dict[str, Any]] = field(default_factory=list)
    final_pass_index: int = 1
    auto_retry_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "retrieval_state": self.retrieval_state,
            "recommendation": self.recommendation,
            "verification_action": self.verification_action,
            "memory_packet": self.memory_packet.to_dict(),
            "query_id": self.query_id,
            "context_markdown": self.context_markdown,
            "task_kind": self.task_kind,
            "temporality": self.temporality,
            "emission": dict(self.emission),
            "retrieval_passes": list(self.retrieval_passes),
            "final_pass_index": self.final_pass_index,
            "auto_retry_summary": dict(self.auto_retry_summary),
        }


@dataclass(slots=True)
class OrchestratorFailureRequest:
    """Input to mark_failure."""
    bridge_id: str
    session_id: str
    task_id: str
    failure_text: str
    task_kind: str = "general"
    query_id: int | None = None
    retrieval_state: str | None = None


@dataclass(slots=True)
class OrchestratorOutcomeRequest:
    """Input to finish_task."""
    bridge_id: str
    session_id: str
    task_id: str
    status: str              # "success" | "failure" | "partial"
    outcome_type: str        # "coding" | "debugging" | "research" | "general" | ...
    summary: str             # structured summary markdown; parsed for writeback
    query_id: int | None = None
    retrieval_state: str | None = None
    recommendation: str | None = None
    useful_ids: list[str] = field(default_factory=list)
    misleading_ids: list[str] = field(default_factory=list)
    fresh_sources_checked: list[str] = field(default_factory=list)
    # Optional writeback overrides — parsed from summary when absent
    note_type: str | None = None
    title: str | None = None
    writeback_intent: str | None = None


@dataclass(slots=True)
class OrchestratorWritebackResult:
    """Output of finish_task."""
    task_id: str
    session_id: str
    outcome_status: str      # mirrors RecordOutcomeResult.status ("accepted")
    candidate_note_ids: list[str]
    decision: str            # "skip" | "confirm" | "update" | "new" | ...
    targets: list[str]
    candidates: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "outcome_status": self.outcome_status,
            "candidate_note_ids": list(self.candidate_note_ids),
            "decision": self.decision,
            "targets": list(self.targets),
            "candidates": list(self.candidates),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Default inference
# ---------------------------------------------------------------------------

_DEBUGGING_KW = frozenset((
    "debug", "fix", "error", "bug", "traceback", "exception",
    "crash", "fail", "broken", "issue", "problem",
))
_CODING_KW = frozenset((
    "implement", "build", "create", "write", "add",
    "refactor", "test", "code", "function", "class",
))
_ARCHITECTURE_KW = frozenset((
    "architect", "design", "structure", "plan",
    "schema", "model", "system", "pattern", "integrate",
))
_RESEARCH_KW = frozenset((
    "research", "investigate", "explain", "what is", "how does",
    "how do", "why", "understand", "overview", "describe",
))

_CURRENT_KINDS = frozenset(("coding", "debugging", "testing", "implementation"))

# Kept as fallback for call sites that don't have config loaded.
# Canonical defaults live in OrchestratorConfig in config.py.
_DEFAULT_TOKEN_BUDGET = 3000
_RESEARCH_TOKEN_BUDGET = 5000


def _infer_task_kind(task_text: str) -> str:
    lower = task_text.lower()
    # Ordered: more specific kinds take priority over general ones.
    for kw in _DEBUGGING_KW:
        if kw in lower:
            return "debugging"
    for kw in _CODING_KW:
        if kw in lower:
            return "coding"
    for kw in _ARCHITECTURE_KW:
        if kw in lower:
            return "architecture"
    for kw in _RESEARCH_KW:
        if kw in lower:
            return "research"
    return "general"


def _infer_temporality(task_kind: str) -> str:
    return "current" if task_kind in _CURRENT_KINDS else "mixed"


def _infer_token_budget(
    task_kind: str,
    explicit: int | None,
    *,
    default_budget: int = _DEFAULT_TOKEN_BUDGET,
    research_budget: int = _RESEARCH_TOKEN_BUDGET,
) -> int:
    if explicit is not None and explicit > 0:
        return explicit
    if task_kind in ("architecture", "research", "knowledge"):
        return research_budget
    return default_budget


# ---------------------------------------------------------------------------
# Context markdown formatter
# ---------------------------------------------------------------------------

def _format_context_markdown(result: Any, *, task_kind: str, temporality: str) -> str:
    """Render a ResolveTaskContextResult into an injectable ## Memory Context block."""
    emission = render_emission(
        result,
        original_task_text=getattr(result, "task_text", ""),
        task_kind=task_kind,
        temporality=temporality,
    )
    return render_single_markdown(emission)


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

def context_result_to_json(result: OrchestratorContextResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


def writeback_result_to_json(result: OrchestratorWritebackResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------

def begin_task(config: AppConfig, req: OrchestratorTaskRequest) -> OrchestratorContextResult:
    """
    Resolve MnemonicOS context before an LLM call.

    Infers task_kind, temporality, and token_budget from req when not supplied.
    Returns a context_markdown string ready to prepend to the system prompt or
    the Claude Code hook context field.
    """
    task_kind = req.task_kind or _infer_task_kind(req.task_text)
    temporality = req.temporality or _infer_temporality(task_kind)
    token_budget = _infer_token_budget(
        task_kind,
        req.token_budget,
        default_budget=config.orchestrator.default_token_budget,
        research_budget=config.orchestrator.research_token_budget,
    )
    # Use config default for max_focused_retries when the request carries the
    # module-level sentinel (DEFAULT_MAX_FOCUSED_RETRIES) unchanged.
    max_focused_retries = (
        config.orchestrator.max_focused_retries
        if req.max_focused_retries == DEFAULT_MAX_FOCUSED_RETRIES
        else req.max_focused_retries
    )

    runtime_req = ResolveTaskContextRequest(
        bridge_id=req.bridge_id,
        session_id=req.session_id,
        task_id=req.task_id,
        task_text=req.task_text,
        task_kind=task_kind,
        temporality=temporality,
        token_budget=token_budget,
        workspace=req.workspace,
        project=req.project,
        repo=req.repo,
        tags=req.tags,
    )

    result = resolve_task_context(config, runtime_req)
    first_emission = render_emission(
        result,
        original_task_text=req.task_text,
        task_kind=task_kind,
        temporality=temporality,
        auto_retry_allowed=req.auto_retry,
        max_focused_retries=max_focused_retries,
        retry_attempt=0,
        previous_queries=[req.task_text],
    )
    retrieval_passes: list[dict[str, Any]] = [
        {
            "pass_index": 1,
            "query_id": result.query_id,
            "query_text": req.task_text,
            "emission": first_emission,
            "triggered_followup_query": first_emission.get("followup_query"),
            "new_ids": first_emission.get("stable_ids", []),
            "cutoff_reason": None,
        }
    ]
    final_result = result
    final_emission = first_emission
    final_pass_index = 1
    auto_retry_summary: dict[str, Any] = {
        "attempted": False,
        "attempt_count": 0,
        "new_ids": [],
        "cutoff_reason": None,
    }

    if should_auto_retry(first_emission):
        followup = first_emission["followup_query"]
        focused_query = str(followup.get("suggested_query") or "").strip()
        if focused_query and focused_query != req.task_text:
            retry_req = ResolveTaskContextRequest(
                bridge_id=req.bridge_id,
                session_id=req.session_id,
                task_id=req.task_id,
                task_text=focused_query,
                task_kind=task_kind,
                temporality=temporality,
                token_budget=token_budget,
                workspace=req.workspace,
                project=req.project,
                repo=req.repo,
                tags=req.tags,
            )
            retry_result = resolve_task_context(config, retry_req)
            retry_emission = render_emission(
                retry_result,
                original_task_text=focused_query,
                task_kind=task_kind,
                temporality=temporality,
                auto_retry_allowed=False,
                max_focused_retries=req.max_focused_retries,
                retry_attempt=1,
                previous_ids=first_emission.get("stable_ids", []),
                previous_queries=[req.task_text, focused_query],
            )
            cutoff = retry_cutoff_reason(
                first_emission,
                retry_emission,
                previous_queries=[req.task_text, focused_query],
                max_focused_retries=max(1, req.max_focused_retries),
                retry_attempt=1,
            )
            if cutoff:
                retry_emission["retry_control"]["cutoff_reason"] = cutoff
            new_ids = retry_emission.get("new_ids", [])
            retrieval_passes.append(
                {
                    "pass_index": 2,
                    "query_id": retry_result.query_id,
                    "query_text": focused_query,
                    "emission": retry_emission,
                    "triggered_followup_query": retry_emission.get("followup_query"),
                    "new_ids": new_ids,
                    "cutoff_reason": cutoff,
                }
            )
            final_result = retry_result
            final_emission = retry_emission
            final_pass_index = 2
            auto_retry_summary = {
                "attempted": True,
                "attempt_count": 1,
                "new_ids": list(new_ids),
                "cutoff_reason": cutoff,
            }

    context_markdown = render_composed_markdown(
        final_emission,
        retrieval_passes=retrieval_passes,
        final_pass_index=final_pass_index,
        auto_retry_summary=auto_retry_summary,
    )

    return OrchestratorContextResult(
        task_id=final_result.task_id,
        session_id=final_result.session_id,
        retrieval_state=final_emission["retrieval_state"],
        recommendation=final_emission["recommendation"],
        verification_action=final_emission["verification_action"],
        memory_packet=final_result.memory_packet,
        query_id=final_result.query_id,
        context_markdown=context_markdown,
        task_kind=task_kind,
        temporality=temporality,
        emission=final_emission,
        retrieval_passes=retrieval_passes,
        final_pass_index=final_pass_index,
        auto_retry_summary=auto_retry_summary,
    )


def mark_failure(
    config: AppConfig,
    req: OrchestratorFailureRequest,
) -> OrchestratorContextResult:
    """
    Record an intermediate failure and re-resolve context.

    Preserves task_id so the full attempt chain is linked in the database.
    The failure_text is used as the re-resolution query so retrieval is focused
    on the specific failure rather than the original broad task.
    Returns a refreshed OrchestratorContextResult for the next attempt.
    """
    # Record before re-resolving so the failure is captured even if retrieval errors.
    record_outcome(
        config,
        RecordOutcomeRequest(
            bridge_id=req.bridge_id,
            session_id=req.session_id,
            task_id=req.task_id,
            outcome_type="intermediate_failure",
            status="failure",
            summary=req.failure_text,
            task_kind=req.task_kind,
            query_id=req.query_id,
            retrieval_state=req.retrieval_state,
        ),
    )

    refresh_req = OrchestratorTaskRequest(
        bridge_id=req.bridge_id,
        session_id=req.session_id,
        task_id=req.task_id,        # ← same identity preserved across retries
        task_text=req.failure_text,
        task_kind=req.task_kind,
    )
    return begin_task(config, refresh_req)


def finish_task(
    config: AppConfig,
    req: OrchestratorOutcomeRequest,
) -> OrchestratorWritebackResult:
    """
    Record the final outcome and compute a writeback decision.

    When note_type and title are not explicitly supplied they are parsed from
    req.summary (using the ````memory` block convention).  Falls back to safe
    defaults so this call never raises on a bare or empty summary.
    Returns an OrchestratorWritebackResult.  Callers hand off to ingest when
    decision != "skip".
    """
    outcome_result = record_outcome(
        config,
        RecordOutcomeRequest(
            bridge_id=req.bridge_id,
            session_id=req.session_id,
            task_id=req.task_id,
            outcome_type=req.outcome_type,
            status=req.status,
            summary=req.summary,
            task_kind=req.outcome_type,
            query_id=req.query_id,
            retrieval_state=req.retrieval_state,
            recommendation=req.recommendation,
            useful_ids=req.useful_ids,
            misleading_ids=req.misleading_ids,
            fresh_sources_checked=req.fresh_sources_checked,
        ),
    )

    note_type, title, summary_text = _extract_writeback_fields(
        req.summary,
        fallback_task_id=req.task_id,
        note_type_hint=req.note_type,
        title_hint=req.title,
    )

    writeback_result = decide_writeback_runtime(
        config,
        DecideWritebackRequest(
            bridge_id=req.bridge_id,
            session_id=req.session_id,
            task_id=req.task_id,
            note_type=note_type,
            title=title,
            summary_text=summary_text,
            task_kind=req.outcome_type,
            retrieval_state=req.retrieval_state,
            writeback_intent=req.writeback_intent,
            fresh_sources_checked=req.fresh_sources_checked,
        ),
    )

    return OrchestratorWritebackResult(
        task_id=req.task_id,
        session_id=req.session_id,
        outcome_status=outcome_result.status,
        candidate_note_ids=outcome_result.candidate_note_ids,
        decision=writeback_result.decision,
        targets=writeback_result.targets,
        candidates=writeback_result.candidates,
        reason=writeback_result.reason,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_writeback_fields(
    summary: str,
    *,
    fallback_task_id: str,
    note_type_hint: str | None,
    title_hint: str | None,
) -> tuple[str, str, str]:
    """
    Parse summary markdown to resolve (note_type, title, body) for writeback.

    Resolution order for each field:
      1. Explicit caller hint (note_type_hint / title_hint)
      2. First item in the ```memory block
      3. Top-level metadata fields (agent:, type:, title:)
      4. Safe defaults ("concept", "Session <task_id>", full summary)
    """
    try:
        from .bridge import parse_summary
        envelope = parse_summary(summary)
        items: list[dict[str, Any]] = envelope.memory_items or []
        if items:
            first = items[0]
            resolved_type = note_type_hint or str(first.get("type") or "concept")
            resolved_title = title_hint or str(first.get("title") or f"Session {fallback_task_id}")
            resolved_body = str(first.get("body") or summary)
            return resolved_type, resolved_title, resolved_body
        # Fall back to header metadata (agent:, type:, title: fields)
        meta = envelope.metadata or {}
        resolved_type = note_type_hint or meta.get("type", "concept")
        resolved_title = title_hint or meta.get("title", f"Session {fallback_task_id}")
        return resolved_type, resolved_title, summary
    except Exception:
        pass

    return (
        note_type_hint or "concept",
        title_hint or f"Session {fallback_task_id}",
        summary,
    )

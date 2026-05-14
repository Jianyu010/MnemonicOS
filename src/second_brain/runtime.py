from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from .assessor import assess_hits as assess_hit_list
from .assessor import assess_packet, assess_payload
from .config import AppConfig
from .db import connect_db, run_migrations
from .models import (
    CandidateMatch,
    DecideWritebackRequest,
    DecideWritebackResult,
    MemoryPacket,
    RecordOutcomeRequest,
    RecordOutcomeResult,
    ResolveTaskContextRequest,
    ResolveTaskContextResult,
    RetrievalAssessment,
    SummaryEnvelope,
    RetrieveHit,
    RetrieveResult,
    RuntimeMemory,
)
from .retrieval import retrieve
from .trust import query_temporality


PROTOCOL_NAME = "mnemos-runtime"
PROTOCOL_VERSION = "0.1"


@dataclass(frozen=True, slots=True)
class TracePurpose:
    intent: str
    required_roles: frozenset[str]
    optional_roles: frozenset[str]
    terminal_preference: str


def assess_retrieval(
    result: RetrieveResult,
    query: str,
    *,
    temporality_override: str | None = None,
) -> RetrievalAssessment:
    temporality = temporality_override or result.temporality or query_temporality(query)
    return assess_hit_list(result.hits, query, temporality_override=temporality)


def assess_retrieval_payload(
    payload: dict[str, Any],
    query: str,
    *,
    temporality_override: str | None = None,
) -> RetrievalAssessment:
    return assess_payload(payload, query, temporality_override=temporality_override)


def resolve_task_context(config: AppConfig, request: ResolveTaskContextRequest) -> ResolveTaskContextResult:
    retrieval_result = retrieve(
        config,
        request.task_text,
        top_k=request.top_k,
        query_type_hint=request.query_type_hint,
        include_archive_fallback=request.include_archive_fallback,
        temporality_override=request.temporality,
        bridge_id=request.bridge_id,
        caller_type_hint=request.caller_type,
    )
    assessment = assess_retrieval(
        retrieval_result,
        request.task_text,
        temporality_override=request.temporality,
    )
    memory_packet = _build_memory_packet(
        retrieval_result,
        assessment=assessment,
        token_budget=request.token_budget,
    )
    memory_packet.same_episode_context = _load_same_episode_context(
        config,
        retrieval_result=retrieval_result,
        memory_packet=memory_packet,
        max_related=_max_related_memories(request.token_budget),
    )
    _complete_trace_context(
        config,
        request=request,
        retrieval_result=retrieval_result,
        memory_packet=memory_packet,
    )
    _finalize_memory_packet(memory_packet, token_budget=request.token_budget)
    directive = assess_packet(
        memory_packet,
        query=request.task_text,
        task_kind=request.task_kind,
        temporality=request.temporality or retrieval_result.temporality,
        query_type=retrieval_result.query_type,
    )
    related_memories = [
        *memory_packet.same_episode_context,
        *memory_packet.related_by_topic,
        *memory_packet.related_by_timeline,
        *memory_packet.related_by_similarity,
        *memory_packet.related_by_contrast,
    ]
    provenance_summary = _provenance_summary(memory_packet)
    _record_runtime_packet_metrics(
        config,
        request=request,
        retrieval_state=directive.retrieval_state,
        recommendation=directive.recommendation,
        query_id=retrieval_result.query_id,
        memory_packet=memory_packet,
    )
    return ResolveTaskContextResult(
        protocol=PROTOCOL_NAME,
        version=PROTOCOL_VERSION,
        operation="resolve_task_context",
        task_id=request.task_id,
        session_id=request.session_id,
        retrieval_state=directive.retrieval_state,
        recommendation=directive.recommendation,
        verification_action=directive.verification_action,
        memory_packet=memory_packet,
        related_memories=related_memories,
        provenance_summary=provenance_summary,
        query_id=retrieval_result.query_id,
        query_type=retrieval_result.query_type,
        classifier_confidence=retrieval_result.classifier_confidence,
        assessment_directive=directive,
        persona=retrieval_result.persona,
        back_feed_hook=retrieval_result.back_feed_hook,
    )


def resolve_task_context_to_json(result: ResolveTaskContextResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


def record_outcome(config: AppConfig, request: RecordOutcomeRequest) -> RecordOutcomeResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    candidate_note_ids = _candidate_note_ids_for_outcome(request)
    with connection:
        connection.execute(
            """
            INSERT INTO task_outcomes(
                task_id, session_id, bridge_id, query_id, task_kind, outcome_type, status,
                summary, fresh_research_needed, fresh_sources_checked, useful_ids,
                misleading_ids, retrieval_state, recommendation, candidate_note_ids,
                chain_id, problem_class, chain_extraction, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(task_id) DO UPDATE SET
                session_id = excluded.session_id,
                bridge_id = excluded.bridge_id,
                query_id = excluded.query_id,
                task_kind = excluded.task_kind,
                outcome_type = excluded.outcome_type,
                status = excluded.status,
                summary = excluded.summary,
                fresh_research_needed = excluded.fresh_research_needed,
                fresh_sources_checked = excluded.fresh_sources_checked,
                useful_ids = excluded.useful_ids,
                misleading_ids = excluded.misleading_ids,
                retrieval_state = excluded.retrieval_state,
                recommendation = excluded.recommendation,
                candidate_note_ids = excluded.candidate_note_ids,
                chain_id = excluded.chain_id,
                problem_class = excluded.problem_class,
                chain_extraction = excluded.chain_extraction,
                updated_at = excluded.updated_at
            """,
            (
                request.task_id,
                request.session_id,
                request.bridge_id,
                request.query_id,
                request.task_kind,
                request.outcome_type,
                request.status,
                request.summary,
                1 if request.fresh_research_needed else 0,
                json.dumps(request.fresh_sources_checked),
                json.dumps(request.useful_ids),
                json.dumps(request.misleading_ids),
                request.retrieval_state,
                request.recommendation,
                json.dumps(candidate_note_ids),
                request.chain_id,
                request.problem_class,
                request.chain_extraction,
            ),
        )
        if request.query_id is not None:
            _apply_outcome_feedback(connection, query_id=request.query_id, request=request)
    connection.close()
    return RecordOutcomeResult(
        protocol=PROTOCOL_NAME,
        version=PROTOCOL_VERSION,
        operation="record_outcome",
        task_id=request.task_id,
        session_id=request.session_id,
        status="accepted",
        candidate_note_ids=candidate_note_ids,
        chain_id=request.chain_id,
    )


def record_outcome_to_json(result: RecordOutcomeResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


def decide_writeback_runtime(config: AppConfig, request: DecideWritebackRequest) -> DecideWritebackResult:
    from .bridge import decide_upsert

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    existing_candidates = _load_candidate_matches(
        connection,
        config,
        note_type=request.note_type,
        requested_ids=request.existing_note_candidates,
        title=request.title,
        summary_text=request.summary_text,
    )
    summary = SummaryEnvelope(
        metadata={
            "task_kind": request.task_kind,
            "mnemos_retrieval_state": request.retrieval_state or "",
            "writeback_intent": request.writeback_intent or "",
            "update_target_ids": ", ".join(request.update_target_ids),
        },
        sections={},
        memory_items=[],
    )
    decision = decide_upsert(
        connection,
        config,
        summary=summary,
        note_type=request.note_type,
        title=request.title,
        summary_text=request.summary_text,
        existing_candidates=existing_candidates,
    )
    connection.close()
    return DecideWritebackResult(
        protocol=PROTOCOL_NAME,
        version=PROTOCOL_VERSION,
        operation="decide_writeback",
        task_id=request.task_id,
        session_id=request.session_id,
        decision=_normalize_writeback_decision(decision.decision, request.writeback_intent),
        targets=decision.targets,
        candidates=decision.candidates,
        reason=decision.reason,
    )


def decide_writeback_to_json(result: DecideWritebackResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


def _build_memory_packet(
    retrieval_result: RetrieveResult,
    *,
    assessment: RetrievalAssessment,
    token_budget: int | None,
) -> MemoryPacket:
    max_primary = _max_primary_memories(token_budget)
    max_related = _max_related_memories(token_budget)
    primary_memories: list[RuntimeMemory] = []
    related_by_topic: list[RuntimeMemory] = []
    related_by_timeline: list[RuntimeMemory] = []
    related_by_similarity: list[RuntimeMemory] = []
    related_by_contrast: list[RuntimeMemory] = []
    archive_context: list[RuntimeMemory] = []
    contradictions: list[dict[str, object]] = []
    warnings: list[str] = []
    verification_needed: list[str] = []
    selected_ids: set[str] = set()
    primary_type_counts: dict[str, int] = {}

    rank_orders = {hit.id: index for index, hit in enumerate(retrieval_result.hits, start=1)}
    archive_hits = _archive_hits_for_packet(retrieval_result)

    for index, hit in enumerate(archive_hits, start=1):
        if len(archive_context) >= max_related:
            break
        archive_context.append(
            _to_runtime_memory(hit, rank_order=index, provenance_extra=["source:raw_session"])
        )

    for hit in retrieval_result.hits:
        if hit.type == "archive_chunk":
            continue
        if hit.freshness_state in {"stale", "contested", "suspect"}:
            warnings.append(_warning_for_hit(hit))
            verification_needed.append(hit.id)
        if hit.freshness_state == "contested":
            contradictions.append(_contradiction_for_hit(hit, source="freshness"))
        if hit.lifecycle_state in {"contested", "archived"}:
            warnings.append(_warning_for_hit(hit))
            verification_needed.append(hit.id)
        if hit.lifecycle_state == "contested":
            contradictions.append(_contradiction_for_hit(hit, source="lifecycle"))
        if len(primary_memories) >= max_primary:
            continue
        bucket = _primary_bucket(hit.type)
        if primary_type_counts.get(bucket, 0) >= _primary_bucket_cap(bucket, max_primary=max_primary):
            continue
        primary_memories.append(_to_runtime_memory(hit, rank_order=rank_orders.get(hit.id)))
        selected_ids.add(hit.id)
        primary_type_counts[bucket] = primary_type_counts.get(bucket, 0) + 1

    if not primary_memories:
        for hit in retrieval_result.hits:
            if hit.type == "archive_chunk":
                continue
            primary_memories.append(_to_runtime_memory(hit, rank_order=rank_orders.get(hit.id)))
            selected_ids.add(hit.id)
            if len(primary_memories) >= max_primary:
                break

    for hit in retrieval_result.hits:
        if hit.type == "archive_chunk" or hit.id in selected_ids:
            continue
        memory = _to_runtime_memory(hit, rank_order=rank_orders.get(hit.id))
        relation_bucket = _relation_bucket(hit)
        if relation_bucket == "timeline" and len(related_by_timeline) < max_related:
            related_by_timeline.append(memory)
        elif relation_bucket == "similarity" and len(related_by_similarity) < max_related:
            related_by_similarity.append(memory)
        elif relation_bucket == "contrast" and len(related_by_contrast) < max_related:
            related_by_contrast.append(memory)
        elif relation_bucket == "topic" and len(related_by_topic) < max_related:
            related_by_topic.append(memory)

    if assessment.state == "insufficient_no_hit":
        warnings.append("No canonical memory hit was strong enough for this task.")
    elif assessment.state == "sufficient_historical":
        warnings.append("Retrieved memory is best treated as historical context.")
    elif assessment.recommendation == "research_fresh":
        warnings.append("Fresh research is recommended before relying on memory.")

    return MemoryPacket(
        primary_memories=primary_memories,
        related_by_topic=related_by_topic,
        related_by_timeline=related_by_timeline,
        related_by_similarity=related_by_similarity,
        related_by_contrast=related_by_contrast,
        archive_context=archive_context,
        contradictions=_dedupe_dicts(contradictions),
        warnings=_dedupe_ordered(warnings),
        verification_needed=_dedupe_ordered(verification_needed),
    )


def _load_same_episode_context(
    config: AppConfig,
    *,
    retrieval_result: RetrieveResult,
    memory_packet: MemoryPacket,
    max_related: int,
) -> list[RuntimeMemory]:
    if max_related <= 0 or not memory_packet.primary_memories:
        return []
    primary = memory_packet.primary_memories[0]
    if not primary.chain_id or primary.type == "archive_chunk":
        return []
    primary_hit = next((hit for hit in retrieval_result.hits if hit.id == primary.id), None)
    if primary_hit is None or primary_hit.final_score <= 0.0:
        return []

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    rows = connection.execute(
        """
        SELECT id, title, type, status, chain_id, lifecycle_state, body_path, summary
        FROM notes
        WHERE chain_id = ? AND id != ?
        ORDER BY
            CASE type
                WHEN 'procedure' THEN 0
                WHEN 'incident' THEN 1
                WHEN 'decision' THEN 2
                WHEN 'concept' THEN 3
                ELSE 4
            END,
            updated_at DESC,
            title ASC
        LIMIT ?
        """,
        (primary.chain_id, primary.id, max_related * 2),
    ).fetchall()
    connection.close()

    context: list[RuntimeMemory] = []
    selected_ids = {item.id for item in memory_packet.primary_memories}
    for row in rows:
        if str(row["id"]) in selected_ids:
            continue
        note_type = str(row["type"])
        if primary.type == "procedure" and note_type == "incident":
            pass
        elif primary.type == "incident" and note_type not in {"procedure", "decision", "concept"}:
            continue
        memory = RuntimeMemory(
            id=str(row["id"]),
            title=str(row["title"]),
            type=note_type,
            summary=str(row["summary"] or ""),
            body_path=str(row["body_path"]),
            chain_id=str(row["chain_id"]) if row["chain_id"] is not None else None,
            freshness_state=None,
            lifecycle_state=str(row["lifecycle_state"]) if row["lifecycle_state"] is not None else None,
            channels=["same_episode"],
            final_score=max(primary_hit.final_score - 0.001 * (len(context) + 1), 0.0),
            relation_types=["part_of_chain"],
            provenance=[f"chain:{primary.chain_id}", "channels:same_episode", "relations:part_of_chain"],
            rank_order=None,
        )
        context.append(memory)
        if len(context) >= max_related:
            break
    return context


TRACE_ALLOWED_RELATIONS = {
    "chain_id",
    "part_of_chain",
    "before",
    "after",
    "supersedes",
    "superseded_by",
    "failed_due_to",
    "attempts_to_solve",
    "supports_solution",
    "resolved_by",
    "derived_from",
    "derived_from_attempt",
    "linked_decision",
    "linked_procedure",
    "linked_note",
    "depends_on",
    "caused_by",
}

TRACE_MAX_DEPTH = 2
TRACE_MAX_ADDED_WIKI_ITEMS = 8
TRACE_MAX_BRANCHES = 3
TRACE_MAX_ITEMS_PER_BRANCH = 6


def _complete_trace_context(
    config: AppConfig,
    *,
    request: ResolveTaskContextRequest,
    retrieval_result: RetrieveResult,
    memory_packet: MemoryPacket,
) -> None:
    purpose = _trace_purpose(request, retrieval_result)
    if purpose.intent == "factual":
        memory_packet.assembly_notes["trace_completion_status"] = "not_needed"
        return
    seeds = _canonical_packet_items(memory_packet)
    if not seeds or not _should_run_trace_completion(purpose, seeds):
        memory_packet.assembly_notes["trace_completion_status"] = "not_needed"
        return

    query_terms = _trace_terms(request.task_text)
    seed_terms = set().union(*[_memory_terms(seed) for seed in seeds]) if seeds else set()
    existing_ids = {item.id for item in seeds}
    added_ids: set[str] = set()
    seed_ids = [seed.id for seed in seeds]
    frontier = [(seed, 0, []) for seed in seeds]
    trace_budget_limited = False
    omitted_relation_families: list[str] = []

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    try:
        while frontier and len(added_ids) < TRACE_MAX_ADDED_WIKI_ITEMS:
            seed, depth, edge_path = frontier.pop(0)
            if depth >= TRACE_MAX_DEPTH:
                trace_budget_limited = True
                continue
            for edge in _trace_edges(connection, seed.id):
                relation = _base_relation(str(edge["relation_type"]))
                if relation not in TRACE_ALLOWED_RELATIONS:
                    omitted_relation_families.append(relation)
                    continue
                target_id = str(edge["target_id"])
                if target_id in existing_ids or target_id in added_ids:
                    continue
                row = _load_trace_note_row(connection, target_id)
                if row is None:
                    continue
                candidate = _runtime_memory_from_trace_row(
                    row,
                    seed=seed,
                    relation_type=relation,
                    depth=depth + 1,
                    edge_path=[*edge_path, f"{seed.id}->{relation}->{target_id}"],
                )
                candidate.assembly_role = _assembly_role(candidate)
                if not _trace_candidate_admitted(
                    candidate,
                    purpose=purpose,
                    query_terms=query_terms,
                    seed_terms=seed_terms,
                    existing_items=seeds,
                    relation_type=relation,
                ):
                    continue
                _add_trace_memory_to_slot(memory_packet, candidate)
                added_ids.add(candidate.id)
                existing_ids.add(candidate.id)
                frontier.append((candidate, depth + 1, list(candidate.edge_path)))
                if len(added_ids) >= TRACE_MAX_ADDED_WIKI_ITEMS:
                    trace_budget_limited = True
                    break
    finally:
        connection.close()

    if trace_budget_limited or omitted_relation_families:
        status = "budget_limited" if trace_budget_limited else "completed"
    else:
        status = "completed" if added_ids else "skipped"
    memory_packet.assembly_notes.update(
        {
            "trace_completion_status": status,
            "trace_seed_item_ids": seed_ids,
            "trace_added_item_ids": _dedupe_ordered(sorted(added_ids)),
            "trace_omitted_relation_families": _dedupe_ordered(omitted_relation_families),
            "trace_budget_limited": trace_budget_limited,
        }
    )


def _trace_purpose(request: ResolveTaskContextRequest, retrieval_result: RetrieveResult) -> TracePurpose:
    text = " ".join([request.task_text, request.task_kind, retrieval_result.query_type or ""]).casefold()
    temporality = (request.temporality or retrieval_result.temporality or "").casefold()
    if temporality == "historical" or _contains_any(text, {"history", "historical", "previous", "previously", "why", "changed"}):
        return TracePurpose(
            intent="historical",
            required_roles=frozenset({"failed_attempt", "decision_pivot", "successful_resolution", "current_reusable_procedure"}),
            optional_roles=frozenset({"diagnosis", "caution", "context"}),
            terminal_preference="timeline",
        )
    if _contains_any(text, {"debug", "debugging", "repair", "recover", "fix", "failure", "failed", "error", "diagnose", "troubleshoot"}):
        return TracePurpose(
            intent="debugging",
            required_roles=frozenset({"failed_attempt", "diagnosis", "successful_resolution", "current_reusable_procedure"}),
            optional_roles=frozenset({"decision_pivot", "caution"}),
            terminal_preference="diagnosis",
        )
    if _contains_any(text, {"compare", "choice", "choose", "alternative", "decision", "why"}):
        return TracePurpose(
            intent="comparison",
            required_roles=frozenset({"decision_pivot", "caution"}),
            optional_roles=frozenset({"successful_resolution", "current_reusable_procedure", "context"}),
            terminal_preference="contrast",
        )
    if request.task_kind in {"coding", "debugging"} or retrieval_result.query_type == "how_to":
        return TracePurpose(
            intent="implementation",
            required_roles=frozenset({"decision_pivot", "current_reusable_procedure"}),
            optional_roles=frozenset({"failed_attempt", "diagnosis", "successful_resolution", "caution", "context"}),
            terminal_preference="success",
        )
    return TracePurpose(
        intent="factual",
        required_roles=frozenset(),
        optional_roles=frozenset(),
        terminal_preference="none",
    )


def _should_run_trace_completion(purpose: TracePurpose, seeds: list[RuntimeMemory]) -> bool:
    if purpose.intent == "factual":
        return False
    if any(seed.chain_id for seed in seeds):
        return True
    if any(set(_normalized_relations(seed)) & TRACE_ALLOWED_RELATIONS for seed in seeds):
        return True
    roles = {seed.assembly_role or _assembly_role(seed) for seed in seeds}
    return bool(roles & (purpose.required_roles | purpose.optional_roles))


def _trace_edges(connection: sqlite3.Connection, note_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT target_id, relation_type
        FROM graph_edges
        WHERE source_note_id = ?
        UNION ALL
        SELECT source_note_id AS target_id, 'reverse_' || relation_type AS relation_type
        FROM graph_edges
        WHERE target_id = ?
        ORDER BY relation_type, target_id
        """,
        (note_id, note_id),
    ).fetchall()


def _load_trace_note_row(connection: sqlite3.Connection, note_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT
            n.id, n.title, n.type, n.status, n.chain_id, n.lifecycle_state,
            n.body_path, n.summary, nf.freshness_state
        FROM notes n
        LEFT JOIN note_freshness nf ON nf.note_id = n.id
        WHERE n.id = ?
          AND (n.status IS NULL OR n.status != 'retired')
          AND (n.valid_to IS NULL OR n.valid_to > date('now'))
        """,
        (note_id,),
    ).fetchone()


def _runtime_memory_from_trace_row(
    row: sqlite3.Row,
    *,
    seed: RuntimeMemory,
    relation_type: str,
    depth: int,
    edge_path: list[str],
) -> RuntimeMemory:
    score = max(seed.final_score - 0.01 * depth, 0.0)
    return RuntimeMemory(
        id=str(row["id"]),
        title=str(row["title"]),
        type=str(row["type"]),
        summary=str(row["summary"] or ""),
        body_path=str(row["body_path"]),
        chain_id=str(row["chain_id"]) if row["chain_id"] is not None else None,
        freshness_state=str(row["freshness_state"]) if row["freshness_state"] is not None else None,
        lifecycle_state=str(row["lifecycle_state"]) if row["lifecycle_state"] is not None else None,
        channels=["trace"],
        final_score=score,
        relation_types=[relation_type],
        provenance=[
            "channels:trace",
            f"relations:{relation_type}",
            f"trace_source:{seed.id}",
            f"trace_depth:{depth}",
        ],
        trace_source="relation_completion",
        trace_depth=depth,
        edge_path=edge_path,
    )


def _trace_candidate_admitted(
    candidate: RuntimeMemory,
    *,
    purpose: TracePurpose,
    query_terms: set[str],
    seed_terms: set[str],
    existing_items: list[RuntimeMemory],
    relation_type: str,
) -> bool:
    role = candidate.assembly_role or _assembly_role(candidate)
    if role not in purpose.required_roles and role not in purpose.optional_roles:
        return False
    candidate_terms = _memory_terms(candidate)
    anchor_match = bool(candidate_terms & query_terms) or bool(candidate_terms & seed_terms)
    if not anchor_match and relation_type not in {
        "failed_due_to",
        "attempts_to_solve",
        "supports_solution",
        "resolved_by",
        "supersedes",
        "superseded_by",
    }:
        return False
    existing_roles = {item.assembly_role or _assembly_role(item) for item in existing_items}
    if role in purpose.required_roles and role not in existing_roles:
        return True
    if relation_type in {"supersedes", "superseded_by"}:
        return True
    if role in {"caution", "diagnosis", "failed_attempt"} and purpose.intent in {"debugging", "implementation", "historical"}:
        return True
    if role in {"current_reusable_procedure", "successful_resolution"} and purpose.intent in {"debugging", "implementation"}:
        return True
    return role in purpose.optional_roles and anchor_match


def _add_trace_memory_to_slot(memory_packet: MemoryPacket, memory: RuntimeMemory) -> None:
    bucket = _relation_bucket(
        RetrieveHit(
            id=memory.id,
            title=memory.title,
            type=memory.type,
            status=None,
            chain_id=memory.chain_id,
            freshness_state=memory.freshness_state,
            lifecycle_state=memory.lifecycle_state,
            body_path=memory.body_path,
            summary=memory.summary,
            channels=memory.channels,
            raw_score=None,
            final_score=memory.final_score,
            excerpt=memory.summary,
            relation_types=memory.relation_types,
        )
    )
    if bucket == "timeline":
        memory_packet.related_by_timeline.append(memory)
    elif bucket == "similarity":
        memory_packet.related_by_similarity.append(memory)
    elif bucket == "contrast":
        memory_packet.related_by_contrast.append(memory)
    else:
        memory_packet.related_by_topic.append(memory)


def _to_runtime_memory(
    hit: RetrieveHit,
    *,
    rank_order: int | None = None,
    provenance_extra: list[str] | None = None,
) -> RuntimeMemory:
    provenance: list[str] = []
    if hit.channels:
        provenance.append(f"channels:{','.join(hit.channels)}")
    if hit.freshness_state:
        provenance.append(f"freshness:{hit.freshness_state}")
    if hit.lifecycle_state:
        provenance.append(f"lifecycle:{hit.lifecycle_state}")
    if hit.relation_types:
        provenance.append(f"relations:{','.join(hit.relation_types)}")
    if hit.chain_id:
        provenance.append(f"chain:{hit.chain_id}")
    if provenance_extra:
        provenance.extend(provenance_extra)
    return RuntimeMemory(
        id=hit.id,
        title=hit.title,
        type=hit.type,
        summary=hit.summary or hit.excerpt,
        body_path=hit.body_path,
        chain_id=hit.chain_id,
        freshness_state=hit.freshness_state,
        lifecycle_state=hit.lifecycle_state,
        channels=list(hit.channels),
        relation_types=list(hit.relation_types),
        final_score=hit.final_score,
        provenance=provenance,
        rank_order=rank_order,
    )


def _provenance_summary(memory_packet: MemoryPacket) -> list[str]:
    summary: list[str] = []
    if memory_packet.primary_memories:
        summary.append(f"primary_memories={len(memory_packet.primary_memories)}")
    if memory_packet.same_episode_context:
        summary.append(f"same_episode_context={len(memory_packet.same_episode_context)}")
    if memory_packet.related_by_topic:
        summary.append(f"related_by_topic={len(memory_packet.related_by_topic)}")
    if memory_packet.related_by_timeline:
        summary.append(f"related_by_timeline={len(memory_packet.related_by_timeline)}")
    if memory_packet.related_by_similarity:
        summary.append(f"related_by_similarity={len(memory_packet.related_by_similarity)}")
    if memory_packet.related_by_contrast:
        summary.append(f"related_by_contrast={len(memory_packet.related_by_contrast)}")
    if memory_packet.archive_context:
        summary.append(f"archive_context={len(memory_packet.archive_context)}")
    if memory_packet.functional_path:
        summary.append(f"functional_path={len(memory_packet.functional_path)}")
    if memory_packet.contradictions:
        summary.append(f"contradictions={len(memory_packet.contradictions)}")
    if memory_packet.verification_needed:
        summary.append(f"verification_needed={len(memory_packet.verification_needed)}")
    return summary


def _warning_for_hit(hit: RetrieveHit) -> str:
    freshness = hit.freshness_state or "unknown"
    lifecycle = hit.lifecycle_state or ""
    if lifecycle in {"contested", "archived"}:
        return f"{hit.id} requires caution due to lifecycle state '{lifecycle}'."
    return f"{hit.id} requires caution due to freshness state '{freshness}'."


def _max_primary_memories(token_budget: int | None) -> int:
    if token_budget is None or token_budget <= 0:
        return 5
    if token_budget < 900:
        return 2
    if token_budget < 1600:
        return 3
    if token_budget < 2400:
        return 4
    return 5


def _max_related_memories(token_budget: int | None) -> int:
    if token_budget is None or token_budget <= 0:
        return 3
    if token_budget < 900:
        return 1
    if token_budget < 1600:
        return 2
    return 3


def _primary_bucket(note_type: str) -> str:
    if note_type == "procedure":
        return "procedure"
    if note_type in {"incident", "journal"}:
        return "episode"
    if note_type in {"decision", "concept", "source"}:
        return "fact"
    return "context"


def _primary_bucket_cap(bucket: str, *, max_primary: int) -> int:
    if max_primary <= 2:
        caps = {"procedure": 1, "fact": 1, "episode": 1, "context": 1}
    elif max_primary == 3:
        caps = {"procedure": 1, "fact": 1, "episode": 1, "context": 1}
    else:
        caps = {"procedure": 2, "fact": 2, "episode": 1, "context": 1}
    return caps.get(bucket, 1)


def _relation_bucket(hit: RetrieveHit) -> str:
    relation_types = {relation.casefold() for relation in hit.relation_types}
    if relation_types & {
        "before",
        "after",
        "reverse_before",
        "reverse_after",
        "supersedes",
        "reverse_supersedes",
    }:
        return "timeline"
    if relation_types & {"similar_to", "reverse_similar_to"}:
        return "similarity"
    if relation_types & {"contrasts_with", "reverse_contrasts_with"}:
        return "contrast"
    if relation_types:
        return "topic"
    if hit.type in {"incident", "journal"}:
        return "timeline"
    return "topic"


def _archive_hits_for_packet(retrieval_result: RetrieveResult) -> list[RetrieveHit]:
    hits: list[RetrieveHit] = []
    seen: set[str] = set()
    for hit in [*retrieval_result.archive_hits, *retrieval_result.hits]:
        if hit.type != "archive_chunk" or hit.id in seen:
            continue
        hits.append(hit)
        seen.add(hit.id)
    return hits


def _contradiction_for_hit(hit: RetrieveHit, *, source: str) -> dict[str, object]:
    return {
        "item_ids": [hit.id],
        "source": source,
        "severity": "high",
        "reason": f"{hit.id} is contested by {source} signal.",
    }


def _finalize_memory_packet(memory_packet: MemoryPacket, *, token_budget: int | None) -> None:
    preexisting_notes = dict(memory_packet.assembly_notes)
    deduped_ids = _dedupe_memory_packet(memory_packet)
    _assign_assembly_roles(memory_packet)
    memory_packet.functional_path = _build_functional_path(memory_packet)
    memory_packet.assessment_reservoir = _build_assessment_reservoir(memory_packet)
    memory_packet.assembly_notes = _build_assembly_notes(
        memory_packet,
        token_budget=token_budget,
        deduped_ids=deduped_ids,
        preexisting_notes=preexisting_notes,
    )
    memory_packet.contradictions = _dedupe_dicts(memory_packet.contradictions)
    memory_packet.warnings = _dedupe_ordered(memory_packet.warnings)
    memory_packet.verification_needed = _dedupe_ordered(memory_packet.verification_needed)


def _dedupe_memory_packet(memory_packet: MemoryPacket) -> list[str]:
    selected: dict[str, RuntimeMemory] = {}
    deduped_ids: list[str] = []

    def dedupe_slot(slot: list[RuntimeMemory]) -> list[RuntimeMemory]:
        deduped: list[RuntimeMemory] = []
        for memory in slot:
            existing = selected.get(memory.id)
            if existing is not None:
                _merge_runtime_memory_metadata(existing, memory)
                deduped_ids.append(memory.id)
                continue
            selected[memory.id] = memory
            deduped.append(memory)
        return deduped

    memory_packet.primary_memories = dedupe_slot(memory_packet.primary_memories)
    memory_packet.same_episode_context = dedupe_slot(memory_packet.same_episode_context)
    memory_packet.related_by_topic = dedupe_slot(memory_packet.related_by_topic)
    memory_packet.related_by_timeline = dedupe_slot(memory_packet.related_by_timeline)
    memory_packet.related_by_similarity = dedupe_slot(memory_packet.related_by_similarity)
    memory_packet.related_by_contrast = dedupe_slot(memory_packet.related_by_contrast)
    memory_packet.archive_context = _dedupe_archive_context(memory_packet.archive_context)
    return _dedupe_ordered(deduped_ids)


def _dedupe_archive_context(slot: list[RuntimeMemory]) -> list[RuntimeMemory]:
    deduped: list[RuntimeMemory] = []
    seen: set[str] = set()
    for memory in slot:
        if memory.type != "archive_chunk" or memory.id in seen:
            continue
        deduped.append(memory)
        seen.add(memory.id)
    return deduped


def _merge_runtime_memory_metadata(target: RuntimeMemory, source: RuntimeMemory) -> None:
    target.channels = _dedupe_ordered([*target.channels, *source.channels])
    target.relation_types = _dedupe_ordered([*target.relation_types, *source.relation_types])
    target.provenance = _dedupe_ordered([*target.provenance, *source.provenance])
    if target.rank_order is None or (source.rank_order is not None and source.rank_order < target.rank_order):
        target.rank_order = source.rank_order
    target.final_score = max(target.final_score, source.final_score)
    if target.trace_source is None:
        target.trace_source = source.trace_source
    if target.trace_depth is None:
        target.trace_depth = source.trace_depth
    target.edge_path = _dedupe_ordered([*target.edge_path, *source.edge_path])


def _assign_assembly_roles(memory_packet: MemoryPacket) -> None:
    all_items = _canonical_packet_items(memory_packet)
    for memory in all_items:
        memory.assembly_role = _assembly_role(memory)
        memory.assembly_order = _assembly_order(memory)


def _canonical_packet_items(memory_packet: MemoryPacket) -> list[RuntimeMemory]:
    return [
        *memory_packet.primary_memories,
        *memory_packet.same_episode_context,
        *memory_packet.related_by_topic,
        *memory_packet.related_by_timeline,
        *memory_packet.related_by_similarity,
        *memory_packet.related_by_contrast,
    ]


def _build_functional_path(memory_packet: MemoryPacket) -> list[dict[str, object]]:
    slot_by_id: dict[str, str] = {}
    for slot_name, slot in [
        ("primary_memories", memory_packet.primary_memories),
        ("same_episode_context", memory_packet.same_episode_context),
        ("related_by_topic", memory_packet.related_by_topic),
        ("related_by_timeline", memory_packet.related_by_timeline),
        ("related_by_similarity", memory_packet.related_by_similarity),
        ("related_by_contrast", memory_packet.related_by_contrast),
    ]:
        for memory in slot:
            slot_by_id.setdefault(memory.id, slot_name)

    items = _canonical_packet_items(memory_packet)
    if not items:
        return []
    path_candidates = [item for item in items if _is_functional_path_candidate(item)]
    if len(path_candidates) < 2:
        chain_groups: dict[str, list[RuntimeMemory]] = {}
        for item in items:
            if item.chain_id:
                chain_groups.setdefault(item.chain_id, []).append(item)
        path_candidates = next((group for group in chain_groups.values() if len(group) >= 2), path_candidates)
    if len(path_candidates) < 2:
        return []

    ordered = sorted(path_candidates, key=lambda item: (item.assembly_order or 9000, item.rank_order or 9999, item.title.casefold(), item.id))
    return [
        {
            "id": item.id,
            "title": item.title,
            "type": item.type,
            "slot": slot_by_id.get(item.id, "unknown"),
            "assembly_role": item.assembly_role or "context",
            "assembly_order": item.assembly_order,
            "rank_order": item.rank_order,
            "final_score": item.final_score,
            "chain_id": item.chain_id,
            "relation_types": list(item.relation_types),
            "trace_source": item.trace_source,
            "trace_depth": item.trace_depth,
            "edge_path": list(item.edge_path),
        }
        for item in ordered
    ]


def _is_functional_path_candidate(memory: RuntimeMemory) -> bool:
    if memory.chain_id:
        return True
    role = memory.assembly_role or _assembly_role(memory)
    if role in {"failed_attempt", "diagnosis", "decision_pivot", "successful_resolution", "current_reusable_procedure", "caution"}:
        return True
    return bool(set(_normalized_relations(memory)) & _FUNCTIONAL_RELATIONS)


def _build_assembly_notes(
    memory_packet: MemoryPacket,
    *,
    token_budget: int | None,
    deduped_ids: list[str],
    preexisting_notes: dict[str, object] | None = None,
) -> dict[str, object]:
    max_primary = _max_primary_memories(token_budget)
    max_related = _max_related_memories(token_budget)
    budget_limited = token_budget is not None and token_budget > 0 and (
        len(memory_packet.primary_memories) >= max_primary
        or len(memory_packet.same_episode_context) >= max_related
        or len(memory_packet.related_by_topic) >= max_related
        or len(memory_packet.related_by_timeline) >= max_related
        or len(memory_packet.related_by_similarity) >= max_related
        or len(memory_packet.related_by_contrast) >= max_related
        or len(memory_packet.archive_context) >= max_related
    )
    warning_item_ids = _dedupe_ordered(
        [
            item.id
            for item in _canonical_packet_items(memory_packet)
            if item.id in memory_packet.verification_needed
            or item.freshness_state in {"stale", "suspect", "contested"}
            or item.lifecycle_state in {"archived", "contested"}
        ]
    )
    path_ids = [str(item["id"]) for item in memory_packet.functional_path]
    ranked_path_ids = [
        item["id"]
        for item in sorted(
            memory_packet.functional_path,
            key=lambda value: (int(value["rank_order"]) if value.get("rank_order") is not None else 9999, str(value["id"])),
        )
    ]
    notes = {
        "primary_empty": not memory_packet.primary_memories,
        "archive_context_present": bool(memory_packet.archive_context),
        "archive_only": bool(memory_packet.archive_context) and not memory_packet.primary_memories,
        "budget_limited": budget_limited,
        "token_budget": token_budget,
        "primary_limit": max_primary,
        "per_related_limit": max_related,
        "body_deferred_item_ids": _body_deferred_item_ids(memory_packet),
        "warning_item_ids": warning_item_ids,
        "contradiction_count": len(memory_packet.contradictions),
        "supersession_item_ids": _supersession_item_ids(memory_packet),
        "deduped_item_ids": deduped_ids,
        "functional_path_present": bool(memory_packet.functional_path),
        "functional_path_confidence": _functional_path_confidence(memory_packet),
        "functional_path_terminal_state": _functional_path_terminal_state(memory_packet),
        "score_order_differs_from_assembly_order": bool(path_ids and path_ids != ranked_path_ids),
        "assessment_reservoir_branch_count": len(memory_packet.assessment_reservoir),
        "assessment_reservoir_item_count": len(
            {
                str(item_id)
                for branch in memory_packet.assessment_reservoir
                for item_id in branch.get("item_ids", [])
            }
        ),
        "default_branch_id": _default_reservoir_branch_id(memory_packet),
        "deferred_branch_ids": [
            str(branch["branch_id"])
            for branch in memory_packet.assessment_reservoir
            if branch.get("defer_reason")
        ],
        "deferred_branch_reasons": {
            str(branch["branch_id"]): str(branch["defer_reason"])
            for branch in memory_packet.assessment_reservoir
            if branch.get("defer_reason")
        },
        "missing_functional_roles": _missing_functional_roles(memory_packet),
    }
    if preexisting_notes:
        notes.update(preexisting_notes)
    notes.setdefault("trace_completion_status", "not_needed")
    notes.setdefault("trace_seed_item_ids", [])
    notes.setdefault("trace_added_item_ids", [])
    notes.setdefault("trace_omitted_relation_families", [])
    notes.setdefault("trace_budget_limited", False)
    return notes


def _build_assessment_reservoir(memory_packet: MemoryPacket) -> list[dict[str, object]]:
    path = memory_packet.functional_path
    if not path:
        return []
    groups: dict[str, list[dict[str, object]]] = {}
    for item in path:
        branch_key = str(item.get("chain_id") or "branch/main")
        groups.setdefault(branch_key, []).append(item)
    branches: list[dict[str, object]] = []
    for index, (branch_key, items) in enumerate(sorted(groups.items()), start=1):
        branch_id = f"branch/{index}"
        terminal_state = _terminal_state_for_path(items)
        confidence = _path_confidence_for_items(items)
        missing_roles = _missing_roles_for_items(items)
        defer_reason = None if index == 1 else "overabundant"
        for item in items:
            memory = _find_memory_by_id(memory_packet, str(item["id"]))
            if memory is not None:
                memory.branch_id = branch_id
                memory.defer_reason = defer_reason
        branches.append(
            {
                "branch_id": branch_id,
                "branch_key": branch_key,
                "seed_item_ids": [
                    str(item["id"])
                    for item in items
                    if not item.get("trace_source")
                ],
                "item_ids": [str(item["id"]) for item in items],
                "edge_path": _dedupe_ordered(
                    [
                        str(step)
                        for item in items
                        for step in item.get("edge_path", [])
                    ]
                ),
                "terminal_state": terminal_state,
                "path_confidence": confidence,
                "default_emission_candidate": index == 1,
                "defer_reason": defer_reason,
                "missing_roles": missing_roles,
                "warning_item_ids": [
                    str(item["id"])
                    for item in items
                    if str(item["id"]) in memory_packet.verification_needed
                ],
                "contradiction_ids": [
                    str(item_id)
                    for contradiction in memory_packet.contradictions
                    for item_id in contradiction.get("item_ids", [])
                    if str(item_id) in {str(item["id"]) for item in items}
                ],
            }
        )
    return branches[:TRACE_MAX_BRANCHES]


def _assembly_role(memory: RuntimeMemory) -> str:
    relations = set(_normalized_relations(memory))
    text = " ".join([memory.title, memory.summary, memory.type]).casefold()
    if memory.type == "archive_chunk":
        return "raw_evidence"
    if memory.freshness_state == "contested" or memory.lifecycle_state == "contested":
        return "caution"
    if relations & {"failed_due_to", "reverse_failed_due_to", "caused_by", "reverse_caused_by"}:
        return "failed_attempt"
    if memory.type in {"incident", "journal"} or _contains_any(text, {"fail", "failed", "failure", "error", "exception", "rollback", "outage"}):
        return "failed_attempt"
    if memory.type == "decision" or relations & {"decided_by", "linked_decision", "reverse_linked_decision"}:
        return "decision_pivot"
    if relations & {"supports_solution", "reverse_supports_solution", "resolved_by", "reverse_resolved_by"} or _contains_any(text, {"success", "successful", "resolved", "resolution", "fixed"}):
        return "successful_resolution"
    if memory.type == "procedure":
        return "current_reusable_procedure"
    if _contains_any(text, {"diagnos", "root cause", "cause", "constraint", "reconcile", "analysis"}):
        return "diagnosis"
    if relations & {"supersedes", "reverse_supersedes", "superseded_by", "reverse_superseded_by", "contrasts_with", "reverse_contrasts_with"}:
        return "caution"
    return "context"


def _assembly_order(memory: RuntimeMemory) -> int:
    role_order = {
        "goal": 10,
        "failed_attempt": 20,
        "diagnosis": 30,
        "decision_pivot": 40,
        "successful_resolution": 50,
        "current_reusable_procedure": 60,
        "caution": 70,
        "context": 80,
        "raw_evidence": 90,
    }
    return role_order.get(memory.assembly_role or "context", 80) * 100 + (memory.rank_order or 99)


def _functional_path_confidence(memory_packet: MemoryPacket) -> str:
    if not memory_packet.functional_path:
        return "none"
    chain_ids = {str(item["chain_id"]) for item in memory_packet.functional_path if item.get("chain_id")}
    relation_count = sum(1 for item in memory_packet.functional_path if item.get("relation_types"))
    if chain_ids or relation_count >= 2:
        return "explicit"
    if relation_count == 1:
        return "partial"
    return "inferred"


def _functional_path_terminal_state(memory_packet: MemoryPacket) -> str:
    if not memory_packet.functional_path:
        return "unknown"
    return _terminal_state_for_path(memory_packet.functional_path)


def _terminal_state_for_path(path_items: list[dict[str, object]]) -> str:
    if not path_items:
        return "unknown"
    roles = [str(item.get("assembly_role") or "") for item in path_items]
    if roles[-1] in {"successful_resolution", "current_reusable_procedure"}:
        return "success"
    if roles[-1] == "failed_attempt":
        return "failure"
    if "failed_attempt" in roles and not ({"successful_resolution", "current_reusable_procedure"} & set(roles)):
        return "unresolved"
    if "failed_attempt" in roles and ({"successful_resolution", "current_reusable_procedure"} & set(roles)):
        return "mixed"
    return "unknown"


def _path_confidence_for_items(path_items: list[dict[str, object]]) -> str:
    if not path_items:
        return "none"
    chain_ids = {str(item["chain_id"]) for item in path_items if item.get("chain_id")}
    relation_count = sum(1 for item in path_items if item.get("relation_types"))
    trace_count = sum(1 for item in path_items if item.get("trace_source"))
    if chain_ids or relation_count >= 2 or trace_count >= 2:
        return "explicit"
    if relation_count == 1 or trace_count == 1:
        return "partial"
    return "inferred"


def _missing_functional_roles(memory_packet: MemoryPacket) -> list[str]:
    return _missing_roles_for_items(memory_packet.functional_path)


def _missing_roles_for_items(path_items: list[dict[str, object]]) -> list[str]:
    roles = {str(item.get("assembly_role") or "") for item in path_items}
    missing: list[str] = []
    if "failed_attempt" in roles and not (roles & {"diagnosis", "decision_pivot"}):
        missing.append("diagnosis")
    if "failed_attempt" in roles and not (roles & {"successful_resolution", "current_reusable_procedure"}):
        missing.append("success_resolution")
    if roles & {"successful_resolution", "current_reusable_procedure"} and not (roles & {"failed_attempt", "decision_pivot", "diagnosis", "context"}):
        missing.append("prerequisite_or_context")
    return _dedupe_ordered(missing)


def _default_reservoir_branch_id(memory_packet: MemoryPacket) -> str | None:
    for branch in memory_packet.assessment_reservoir:
        if branch.get("default_emission_candidate"):
            return str(branch.get("branch_id"))
    return None


def _find_memory_by_id(memory_packet: MemoryPacket, memory_id: str) -> RuntimeMemory | None:
    for item in _canonical_packet_items(memory_packet):
        if item.id == memory_id:
            return item
    return None


def _supersession_item_ids(memory_packet: MemoryPacket) -> list[str]:
    ids: list[str] = []
    supersession = {"supersedes", "reverse_supersedes", "superseded_by", "reverse_superseded_by"}
    for item in _canonical_packet_items(memory_packet):
        if set(_normalized_relations(item)) & supersession:
            ids.append(item.id)
    return _dedupe_ordered(ids)


def _body_deferred_item_ids(memory_packet: MemoryPacket) -> list[str]:
    return _dedupe_ordered(
        [
            item.id
            for item in [*_canonical_packet_items(memory_packet), *memory_packet.archive_context]
            if item.body_path
        ]
    )


def _normalized_relations(memory: RuntimeMemory) -> list[str]:
    return [relation.casefold() for relation in memory.relation_types]


def _base_relation(relation: str) -> str:
    value = relation.casefold()
    while value.startswith("reverse_"):
        value = value.removeprefix("reverse_")
    return value


TRACE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "we",
    "what",
    "when",
    "where",
    "who",
    "why",
}


def _trace_terms(text: str) -> set[str]:
    normalized = "".join(ch.casefold() if ch.isalnum() else " " for ch in text)
    return {part for part in normalized.split() if len(part) >= 3 and part not in TRACE_STOPWORDS}


def _memory_terms(memory: RuntimeMemory) -> set[str]:
    return _trace_terms(" ".join([memory.id.replace("/", " "), memory.title, memory.summary, memory.type]))


def _contains_any(text: str, needles: set[str]) -> bool:
    return any(needle in text for needle in needles)


_FUNCTIONAL_RELATIONS = {
    "before",
    "after",
    "reverse_before",
    "reverse_after",
    "supersedes",
    "reverse_supersedes",
    "superseded_by",
    "reverse_superseded_by",
    "failed_due_to",
    "reverse_failed_due_to",
    "attempts_to_solve",
    "reverse_attempts_to_solve",
    "supports_solution",
    "reverse_supports_solution",
    "derived_from",
    "reverse_derived_from",
    "linked_decision",
    "reverse_linked_decision",
}


def _dedupe_ordered(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def _dedupe_dicts(values: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, sort_keys=True)
        if key in seen:
            continue
        deduped.append(value)
        seen.add(key)
    return deduped


def _candidate_note_ids_for_outcome(request: RecordOutcomeRequest) -> list[str]:
    return _dedupe_ordered([*request.useful_ids, *request.misleading_ids])


def _apply_outcome_feedback(connection: sqlite3.Connection, *, query_id: int, request: RecordOutcomeRequest) -> None:
    useful_ids = set(request.useful_ids)
    misleading_ids = set(request.misleading_ids)
    top_hit = connection.execute(
        "SELECT note_id FROM retrieval_hits WHERE query_id = ? ORDER BY rank ASC LIMIT 1",
        (query_id,),
    ).fetchone()
    top1_correct: int | None = None
    if top_hit is not None:
        top1_correct = 1 if str(top_hit["note_id"]) in useful_ids else 0 if useful_ids else None
    useful_label: int | None = None
    if request.status.casefold() == "success":
        useful_label = 1
    elif request.status.casefold() in {"failure", "error"}:
        useful_label = 0
    connection.execute(
        """
        UPDATE retrieval_queries
        SET useful = COALESCE(?, useful),
            top1_correct = COALESCE(?, top1_correct)
        WHERE id = ?
        """,
        (useful_label, top1_correct, query_id),
    )
    for note_id in useful_ids:
        connection.execute(
            "UPDATE retrieval_hits SET useful = 1 WHERE query_id = ? AND note_id = ?",
            (query_id, note_id),
        )
    for note_id in misleading_ids:
        connection.execute(
            "UPDATE retrieval_hits SET useful = 0 WHERE query_id = ? AND note_id = ?",
            (query_id, note_id),
        )


def _load_candidate_matches(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    note_type: str,
    requested_ids: list[str],
    title: str,
    summary_text: str,
) -> list[CandidateMatch]:
    from .bridge import find_existing_candidates

    if not requested_ids:
        return find_existing_candidates(
            connection,
            config,
            note_type=note_type,
            title=title,
            summary=summary_text,
        )
    rows = connection.execute(
        f"""
        SELECT id, title, type
        FROM notes
        WHERE id IN ({','.join('?' for _ in requested_ids)})
        """,
        tuple(requested_ids),
    ).fetchall()
    matches: list[CandidateMatch] = []
    for row in rows:
        matches.append(
            CandidateMatch(
                note_id=str(row["id"]),
                title=str(row["title"]),
                note_type=str(row["type"]),
                score=0.95,
                reason="explicit_candidate",
            )
        )
    return matches


def _normalize_writeback_decision(decision: str, explicit_intent: str | None) -> str:
    if explicit_intent:
        normalized = explicit_intent.strip().casefold()
        if normalized in {"skip", "confirm", "update", "new", "incident_only", "procedure_refinement"}:
            return normalized
    return {
        "skip_duplicate": "skip",
        "confirm_existing": "confirm",
        "update_existing": "update",
        "new_note": "new",
        "needs_review": "update",
    }.get(decision, decision)


def _record_runtime_packet_metrics(
    config: AppConfig,
    *,
    request: ResolveTaskContextRequest,
    retrieval_state: str,
    recommendation: str,
    query_id: int,
    memory_packet: MemoryPacket,
) -> None:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")
    with connection:
        connection.execute(
            """
            INSERT INTO runtime_packet_metrics(
                query_id, task_id, session_id, task_kind, retrieval_state, recommendation,
                primary_memory_count, same_episode_context_count, related_memory_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query_id) DO UPDATE SET
                task_id = excluded.task_id,
                session_id = excluded.session_id,
                task_kind = excluded.task_kind,
                retrieval_state = excluded.retrieval_state,
                recommendation = excluded.recommendation,
                primary_memory_count = excluded.primary_memory_count,
                same_episode_context_count = excluded.same_episode_context_count,
                related_memory_count = excluded.related_memory_count
            """,
            (
                query_id,
                request.task_id,
                request.session_id,
                request.task_kind,
                retrieval_state,
                recommendation,
                len(memory_packet.primary_memories),
                len(memory_packet.same_episode_context),
                len(memory_packet.related_by_topic)
                + len(memory_packet.related_by_timeline)
                + len(memory_packet.related_by_similarity)
                + len(memory_packet.related_by_contrast),
            ),
        )
    connection.close()

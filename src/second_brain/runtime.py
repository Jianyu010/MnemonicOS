from __future__ import annotations

import json
import sqlite3
from typing import Any

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
DEFAULT_VERIFICATION_ACTION = "none"


def assess_retrieval(
    result: RetrieveResult,
    query: str,
    *,
    temporality_override: str | None = None,
) -> RetrievalAssessment:
    temporality = temporality_override or query_temporality(query)
    return _assess_hits(result.hits, temporality=temporality)


def assess_retrieval_payload(
    payload: dict[str, Any],
    query: str,
    *,
    temporality_override: str | None = None,
) -> RetrievalAssessment:
    temporality = temporality_override or query_temporality(query)
    hits: list[RetrieveHit] = []
    for raw_hit in payload.get("hits", []):
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
    return _assess_hits(hits, temporality=temporality)


def resolve_task_context(config: AppConfig, request: ResolveTaskContextRequest) -> ResolveTaskContextResult:
    retrieval_result = retrieve(
        config,
        request.task_text,
        top_k=request.top_k,
        query_type_hint=request.query_type_hint,
        include_archive_fallback=request.include_archive_fallback,
        temporality_override=request.temporality,
    )
    assessment = assess_retrieval(
        retrieval_result,
        request.task_text,
        temporality_override=request.temporality,
    )
    verification_action = _verification_action(assessment)
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
        retrieval_state=assessment.state,
        recommendation=assessment.recommendation,
        query_id=retrieval_result.query_id,
        memory_packet=memory_packet,
    )
    return ResolveTaskContextResult(
        protocol=PROTOCOL_NAME,
        version=PROTOCOL_VERSION,
        operation="resolve_task_context",
        task_id=request.task_id,
        session_id=request.session_id,
        retrieval_state=assessment.state,
        recommendation=assessment.recommendation,
        verification_action=verification_action,
        memory_packet=memory_packet,
        related_memories=related_memories,
        provenance_summary=provenance_summary,
        query_id=retrieval_result.query_id,
        query_type=retrieval_result.query_type,
        classifier_confidence=retrieval_result.classifier_confidence,
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


def _assess_hits(hits: list[RetrieveHit], *, temporality: str) -> RetrievalAssessment:
    if not hits:
        return RetrievalAssessment(
            state="insufficient_no_hit",
            recommendation="research_fresh",
            hit_ids=[],
            stale_ids=[],
            contested_ids=[],
            current_ids=[],
            partial_ids=[],
        )

    stale_ids: list[str] = []
    contested_ids: list[str] = []
    current_ids: list[str] = []
    partial_ids: list[str] = []
    note_hits = [hit for hit in hits if hit.type != "archive_chunk"]
    hit_ids = [hit.id for hit in note_hits]

    if not note_hits:
        return RetrievalAssessment(
            state="insufficient_no_hit",
            recommendation="research_fresh",
            hit_ids=[],
            stale_ids=[],
            contested_ids=[],
            current_ids=[],
            partial_ids=[],
        )

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
            state="sufficient_historical",
            recommendation="reuse",
            hit_ids=hit_ids,
            stale_ids=stale_ids,
            contested_ids=contested_ids,
            current_ids=current_ids,
            partial_ids=partial_ids,
        )

    top_hit = note_hits[0]
    top_freshness = top_hit.freshness_state or "fresh"
    if top_freshness == "contested" and not current_ids:
        state = "insufficient_contested"
        recommendation = "research_fresh"
    elif top_freshness == "stale" and not current_ids:
        state = "insufficient_stale"
        recommendation = "research_fresh"
    elif current_ids and not partial_ids:
        state = "sufficient_current"
        recommendation = "reuse"
    elif current_ids or partial_ids:
        state = "partial"
        recommendation = "reuse_with_verification"
    elif contested_ids:
        state = "insufficient_contested"
        recommendation = "research_fresh"
    else:
        state = "insufficient_stale"
        recommendation = "research_fresh"

    return RetrievalAssessment(
        state=state,
        recommendation=recommendation,
        hit_ids=hit_ids,
        stale_ids=stale_ids,
        contested_ids=contested_ids,
        current_ids=current_ids,
        partial_ids=partial_ids,
    )


def _verification_action(assessment: RetrievalAssessment) -> str:
    if assessment.state == "sufficient_historical":
        return "treat_as_historical_only"
    if assessment.recommendation == "reuse":
        return DEFAULT_VERIFICATION_ACTION
    if assessment.state == "partial":
        return "verify_current_truth"
    if assessment.state in {"insufficient_stale", "insufficient_contested"}:
        return "check_recent_sources"
    return "check_recent_sources"


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
    warnings: list[str] = []
    verification_needed: list[str] = []
    selected_ids: set[str] = set()
    primary_type_counts: dict[str, int] = {}

    for hit in retrieval_result.hits:
        if hit.type == "archive_chunk":
            continue
        if hit.freshness_state in {"stale", "contested", "suspect"}:
            warnings.append(_warning_for_hit(hit))
            verification_needed.append(hit.id)
        if hit.lifecycle_state in {"contested", "archived"}:
            warnings.append(_warning_for_hit(hit))
            verification_needed.append(hit.id)
        if len(primary_memories) >= max_primary:
            continue
        bucket = _primary_bucket(hit.type)
        if primary_type_counts.get(bucket, 0) >= _primary_bucket_cap(bucket, max_primary=max_primary):
            continue
        primary_memories.append(_to_runtime_memory(hit))
        selected_ids.add(hit.id)
        primary_type_counts[bucket] = primary_type_counts.get(bucket, 0) + 1

    if not primary_memories:
        for hit in retrieval_result.hits:
            if hit.type == "archive_chunk":
                continue
            primary_memories.append(_to_runtime_memory(hit))
            selected_ids.add(hit.id)
            if len(primary_memories) >= max_primary:
                break

    for hit in retrieval_result.hits:
        if hit.type == "archive_chunk" or hit.id in selected_ids:
            continue
        memory = _to_runtime_memory(hit)
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
        )
        context.append(memory)
        if len(context) >= max_related:
            break
    return context


def _to_runtime_memory(hit: RetrieveHit) -> RuntimeMemory:
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


def _dedupe_ordered(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
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

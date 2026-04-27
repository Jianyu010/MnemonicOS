from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class NoteRecord:
    id: str
    type: str
    title: str
    status: str | None
    lifecycle_state: str | None
    overlay_class: str | None
    confidence: str | None
    tags: list[str]
    entities: list[str]
    entity_relations: list[tuple[str, str]]
    source_refs: list[str]
    valid_from: str | None
    valid_to: str | None
    updated_at: str
    created_at: str
    last_verified_at: str | None
    verified_by: str | None
    last_observed_at: str | None
    chain_id: str | None
    chain_confidence: str | None
    chain_question: str | None
    chain_summary: str | None
    problem_class: str | None
    failure_summary: str | None
    attempt_summaries: list[str]
    solution_rationale: str | None
    summary: str
    aliases: list[str]
    obsidian_links: list[str]
    body: str
    body_path: Path
    content_hash: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["body_path"] = str(self.body_path)
        return payload


@dataclass(slots=True)
class SyncResult:
    scanned_paths: int
    synced_notes: int
    deleted_notes: int
    parse_errors: int


@dataclass(slots=True)
class RetrieveHit:
    id: str
    title: str
    type: str
    status: str | None
    chain_id: str | None
    freshness_state: str | None
    lifecycle_state: str | None
    body_path: str
    summary: str
    channels: list[str]
    raw_score: float | None
    final_score: float
    excerpt: str
    relation_types: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RetrieveResult:
    query_id: int
    query_type: str | None
    classifier_confidence: float | None
    hits: list[RetrieveHit]
    pinned_paths: list[str]


@dataclass(slots=True)
class IngestSessionResult:
    archive_id: str
    raw_path: str
    chunk_count: int
    promoted_notes: list[str]
    review_items: list[str]


@dataclass(slots=True)
class IngestArtifactResult:
    archive_id: str
    raw_path: str
    manifest_path: str
    ingest_status: str
    source_format: str
    figure_count: int
    warnings: list[str]
    failure_reason: str | None
    needs_visual_pass: bool
    needs_manual_projection: bool
    image_only_pdf: bool


@dataclass(slots=True)
class JobRunResult:
    job_name: str
    status: str
    stats: dict[str, int]
    review_items_created: list[str]


@dataclass(slots=True)
class RetrievalAssessment:
    state: str
    recommendation: str
    hit_ids: list[str]
    stale_ids: list[str]
    contested_ids: list[str]
    current_ids: list[str]
    partial_ids: list[str]


@dataclass(slots=True)
class SummaryEnvelope:
    metadata: dict[str, str]
    sections: dict[str, str]
    memory_items: list[dict[str, Any]]


@dataclass(slots=True)
class CandidateMatch:
    note_id: str
    title: str
    note_type: str
    score: float
    reason: str


@dataclass(slots=True)
class UpsertDecision:
    decision: str
    targets: list[str]
    candidates: list[str]
    reason: str


@dataclass(slots=True)
class RuntimeMemory:
    id: str
    title: str
    type: str
    summary: str
    body_path: str
    chain_id: str | None
    freshness_state: str | None
    lifecycle_state: str | None
    channels: list[str]
    final_score: float
    relation_types: list[str] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MemoryPacket:
    primary_memories: list[RuntimeMemory] = field(default_factory=list)
    same_episode_context: list[RuntimeMemory] = field(default_factory=list)
    related_by_topic: list[RuntimeMemory] = field(default_factory=list)
    related_by_timeline: list[RuntimeMemory] = field(default_factory=list)
    related_by_similarity: list[RuntimeMemory] = field(default_factory=list)
    related_by_contrast: list[RuntimeMemory] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verification_needed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "primary_memories": [asdict(item) for item in self.primary_memories],
            "same_episode_context": [asdict(item) for item in self.same_episode_context],
            "related_by_topic": [asdict(item) for item in self.related_by_topic],
            "related_by_timeline": [asdict(item) for item in self.related_by_timeline],
            "related_by_similarity": [asdict(item) for item in self.related_by_similarity],
            "related_by_contrast": [asdict(item) for item in self.related_by_contrast],
            "warnings": list(self.warnings),
            "verification_needed": list(self.verification_needed),
        }


@dataclass(slots=True)
class ResolveTaskContextRequest:
    bridge_id: str
    session_id: str
    task_id: str
    task_text: str
    task_kind: str = "general"
    temporality: str | None = None
    token_budget: int | None = None
    workspace: str | None = None
    project: str | None = None
    repo: str | None = None
    tags: list[str] = field(default_factory=list)
    include_archive_fallback: bool = True
    top_k: int | None = None
    query_type_hint: str | None = None


@dataclass(slots=True)
class ResolveTaskContextResult:
    protocol: str
    version: str
    operation: str
    task_id: str
    session_id: str
    retrieval_state: str
    recommendation: str
    verification_action: str
    memory_packet: MemoryPacket
    related_memories: list[RuntimeMemory]
    provenance_summary: list[str]
    query_id: int
    query_type: str | None
    classifier_confidence: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "operation": self.operation,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "retrieval_state": self.retrieval_state,
            "recommendation": self.recommendation,
            "verification_action": self.verification_action,
            "memory_packet": self.memory_packet.to_dict(),
            "related_memories": [asdict(item) for item in self.related_memories],
            "provenance_summary": list(self.provenance_summary),
            "debug": {
                "query_id": self.query_id,
                "query_type": self.query_type,
                "classifier_confidence": self.classifier_confidence,
            },
        }


@dataclass(slots=True)
class RecordOutcomeRequest:
    bridge_id: str
    session_id: str
    task_id: str
    outcome_type: str
    status: str
    summary: str
    task_kind: str = "general"
    query_id: int | None = None
    retrieval_state: str | None = None
    recommendation: str | None = None
    fresh_research_needed: bool = False
    fresh_sources_checked: list[str] = field(default_factory=list)
    useful_ids: list[str] = field(default_factory=list)
    misleading_ids: list[str] = field(default_factory=list)
    chain_id: str | None = None
    problem_class: str | None = None
    chain_extraction: str | None = None


@dataclass(slots=True)
class RecordOutcomeResult:
    protocol: str
    version: str
    operation: str
    task_id: str
    session_id: str
    status: str
    candidate_note_ids: list[str]
    chain_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "operation": self.operation,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "status": self.status,
            "chain_id": self.chain_id,
            "writeback_hint": {
                "recommended_operation": "decide_writeback",
                "candidate_note_ids": list(self.candidate_note_ids),
            },
        }


@dataclass(slots=True)
class DecideWritebackRequest:
    bridge_id: str
    session_id: str
    task_id: str
    note_type: str
    title: str
    summary_text: str
    task_kind: str = "general"
    retrieval_state: str | None = None
    writeback_intent: str | None = None
    existing_note_candidates: list[str] = field(default_factory=list)
    update_target_ids: list[str] = field(default_factory=list)
    fresh_research_needed: bool = False
    fresh_sources_checked: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DecideWritebackResult:
    protocol: str
    version: str
    operation: str
    task_id: str
    session_id: str
    decision: str
    targets: list[str]
    candidates: list[str]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "operation": self.operation,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "decision": self.decision,
            "targets": list(self.targets),
            "candidates": list(self.candidates),
            "reason": self.reason,
        }

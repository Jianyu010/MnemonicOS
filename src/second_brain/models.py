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
    corroboration_count: int

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
    relevance_score: float = 0.0
    quality_score: float = 0.0


@dataclass(slots=True)
class RetrieveResult:
    query_id: int
    query_type: str | None
    classifier_confidence: float | None
    hits: list[RetrieveHit]
    pinned_paths: list[str]
    temporality: str | None = None
    persona: str | None = None
    back_feed_hook: str | None = None
    archive_hits: list[RetrieveHit] = field(default_factory=list)
    suppressed_by_gate_count: int = 0
    passed_gate_count: int = 0


@dataclass(slots=True)
class RecallCandidate:
    id: str
    title: str
    type: str
    status: str | None
    chain_id: str | None
    lifecycle_state: str | None
    body_path: str
    summary: str
    body: str
    channels: set[str] = field(default_factory=set)
    relation_types: list[str] = field(default_factory=list)
    graph_depth: int | None = None
    seed_from_ids: list[str] = field(default_factory=list)
    exact_rank: int | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    semantic_score: float | None = None
    graph_score: float | None = None
    learned_score: float | None = None
    freshness_score: float | None = None
    freshness_state: str | None = None
    trust_score: float | None = None
    type_prior_score: float | None = None
    updated_at: str | None = None
    created_at: str | None = None
    last_verified_at: str | None = None


@dataclass(slots=True)
class ArchiveCandidate:
    id: str
    archive_id: str
    chunk_index: int
    archive_path: str
    summary: str
    excerpt: str
    channels: set[str] = field(default_factory=set)
    bm25_rank: int | None = None
    bm25_score: float | None = None


@dataclass(frozen=True, slots=True)
class ArchivePolicyDecision:
    allowed: bool
    mode: str
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RecallMetrics:
    exact_count: int = 0
    bm25_count: int = 0
    semantic_count: int = 0
    graph_count: int = 0
    archive_count: int = 0
    merged_wiki_count: int = 0
    seed_count: int = 0
    graph_seed_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CandidatePool:
    parsed_query: Any
    wiki_candidates: list[RecallCandidate] = field(default_factory=list)
    archive_candidates: list[ArchiveCandidate] = field(default_factory=list)
    recall_metrics: RecallMetrics = field(default_factory=RecallMetrics)
    archive_policy: ArchivePolicyDecision = field(
        default_factory=lambda: ArchivePolicyDecision(allowed=False, mode="disabled", reasons=[])
    )


@dataclass(slots=True)
class NoteSignals:
    trust_score: float = 0.0
    freshness_score: float = 0.0
    freshness_state: str = "fresh"
    importance_score: float = 0.0
    type_prior_score: float = 0.0
    summary_quality: str = "none"
    quality_pass: bool = False
    quality_reasons: list[str] = field(default_factory=list)
    freshness_multiplier: float = 1.0
    passive_feedback_state: str = "none"
    passive_feedback_confidence: float = 0.0


@dataclass(slots=True)
class SignalEnrichedCandidate:
    candidate: RecallCandidate
    signals: NoteSignals


@dataclass(slots=True)
class ArchiveSignals:
    summary_quality: str = "none"
    quality_pass: bool = False
    quality_reasons: list[str] = field(default_factory=list)
    passive_feedback_state: str = "none"
    passive_feedback_confidence: float = 0.0


@dataclass(slots=True)
class SignalEnrichedArchiveCandidate:
    candidate: ArchiveCandidate
    signals: ArchiveSignals


@dataclass(slots=True)
class SignalEnrichedCandidatePool:
    parsed_query: Any
    wiki_candidates: list[SignalEnrichedCandidate] = field(default_factory=list)
    archive_candidates: list[SignalEnrichedArchiveCandidate] = field(default_factory=list)
    recall_metrics: RecallMetrics = field(default_factory=RecallMetrics)
    archive_policy: ArchivePolicyDecision = field(
        default_factory=lambda: ArchivePolicyDecision(allowed=False, mode="disabled", reasons=[])
    )


@dataclass(slots=True)
class RerankFeatureRow:
    note_id: str
    exact_hit: int
    bm25_rank: int | None
    bm25_score: float | None
    semantic_score: float | None
    graph_score: float | None
    freshness_score: float
    type_prior_score: float
    trust_score: float
    importance_score: float
    quality_pass: bool
    quality_score: float
    passive_feedback_state: str
    passive_feedback_confidence: float


@dataclass(slots=True)
class ScoredCandidate:
    candidate: RecallCandidate
    signals: NoteSignals
    hit: RetrieveHit
    feature_row: RerankFeatureRow


@dataclass(slots=True)
class ScoredArchiveCandidate:
    candidate: ArchiveCandidate
    signals: ArchiveSignals
    hit: RetrieveHit


@dataclass(slots=True)
class RankedHits:
    wiki_hits: list[ScoredCandidate] = field(default_factory=list)
    archive_hits: list[ScoredArchiveCandidate] = field(default_factory=list)
    suppressed_by_gate_count: int = 0
    passed_gate_count: int = 0

    @property
    def hits(self) -> list[RetrieveHit]:
        return [item.hit for item in self.wiki_hits] if self.wiki_hits else [item.hit for item in self.archive_hits]


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
    duplicate_state: str = "unique"
    canonical_archive_id: str | None = None


@dataclass(slots=True)
class BeginEpisodeRequest:
    bridge_id: str
    conversation_id: str | None = None
    episode_id: str | None = None
    parent_conversation_id: str | None = None
    related_conversation_ids: list[str] = field(default_factory=list)
    task_id: str | None = None
    cwd: str | None = None
    repo: str | None = None
    branch: str | None = None
    model: str | None = None
    agent_runtime: str | None = None
    platform_thread_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BeginEpisodeResult:
    conversation_id: str
    episode_id: str
    conversation_path: str
    episode_path: str
    events_path: str
    transcript_path: str
    artifacts_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class AppendEventRequest:
    bridge_id: str
    conversation_id: str
    episode_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    branch_id: str = "main"
    timestamp: str | None = None


@dataclass(slots=True)
class AppendEventResult:
    conversation_id: str
    episode_id: str
    seq: int
    events_path: str
    transcript_path: str
    artifact_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class EndEpisodeRequest:
    bridge_id: str
    conversation_id: str
    episode_id: str
    status: str = "success"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EndEpisodeResult:
    conversation_id: str
    episode_id: str
    status: str
    event_count: int
    content_hash: str
    manifest_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


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
class AssessmentDirective:
    retrieval_state: str
    completion_state: str
    reuse_mode: str
    recommendation: str
    verification_action: str
    emission_profile: str
    hydration_policy: str
    selected_branch_ids: list[str] = field(default_factory=list)
    deferred_branch_ids: list[str] = field(default_factory=list)
    missing_roles: list[str] = field(default_factory=list)
    followup_query_policy: str = "none"
    fresh_pipeline_threshold: str = "none"
    assessment_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class AssessedPacket:
    packet: "MemoryPacket"
    directive: AssessmentDirective

    def to_dict(self) -> dict[str, object]:
        return {
            "packet": self.packet.to_dict(),
            "directive": self.directive.to_dict(),
        }


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
    rank_order: int | None = None
    assembly_role: str | None = None
    assembly_order: int | None = None
    trace_source: str | None = None
    trace_depth: int | None = None
    edge_path: list[str] = field(default_factory=list)
    branch_id: str | None = None
    defer_reason: str | None = None


@dataclass(slots=True)
class MemoryPacket:
    primary_memories: list[RuntimeMemory] = field(default_factory=list)
    same_episode_context: list[RuntimeMemory] = field(default_factory=list)
    related_by_topic: list[RuntimeMemory] = field(default_factory=list)
    related_by_timeline: list[RuntimeMemory] = field(default_factory=list)
    related_by_similarity: list[RuntimeMemory] = field(default_factory=list)
    related_by_contrast: list[RuntimeMemory] = field(default_factory=list)
    archive_context: list[RuntimeMemory] = field(default_factory=list)
    contradictions: list[dict[str, object]] = field(default_factory=list)
    functional_path: list[dict[str, object]] = field(default_factory=list)
    assessment_reservoir: list[dict[str, object]] = field(default_factory=list)
    assembly_notes: dict[str, object] = field(default_factory=dict)
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
            "archive_context": [asdict(item) for item in self.archive_context],
            "contradictions": list(self.contradictions),
            "functional_path": list(self.functional_path),
            "assessment_reservoir": list(self.assessment_reservoir),
            "assembly_notes": dict(self.assembly_notes),
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
    caller_type: str | None = None


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
    assessment_directive: AssessmentDirective | None = None
    persona: str | None = None
    back_feed_hook: str | None = None

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
            "assessment_directive": self.assessment_directive.to_dict() if self.assessment_directive else None,
            "memory_packet": self.memory_packet.to_dict(),
            "related_memories": [asdict(item) for item in self.related_memories],
            "provenance_summary": list(self.provenance_summary),
            "debug": {
                "query_id": self.query_id,
                "query_type": self.query_type,
                "classifier_confidence": self.classifier_confidence,
                "persona": self.persona,
                "back_feed_hook": self.back_feed_hook,
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

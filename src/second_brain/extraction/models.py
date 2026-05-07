from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SUPPORTED_CANDIDATE_TYPES = {"source", "concept", "procedure", "decision", "incident"}
SUPPORTED_CONFIDENCE = {"low", "medium", "high"}
SUPPORTED_COMPONENT_TYPES = {
    "user_request",
    "agent_action",
    "failure",
    "observation",
    "web_finding",
    "milestone",
    "instruction",
    "definition",
    "claim",
    "experiment",
    "derivation",
    "theorem",
    "example",
    "procedure_step",
    "open_question",
}
SCHEMA_VERSION = "mnemos-extraction-v1"


@dataclass(slots=True)
class ExtractionQueueItem:
    id: int
    source_kind: str
    archive_id: str | None
    conversation_id: str | None
    episode_id: str | None
    bridge_id: str | None
    session_id: str | None
    task_id: str | None
    duplicate_state: str | None
    status: str
    attempt_count: int
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VisualSnippet:
    artifact_id: str
    marker: str
    source_locator: str
    visual_description: str
    visible_text: str = ""
    warnings: list[str] = field(default_factory=list)
    model_metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "marker": self.marker,
            "source_locator": self.source_locator,
            "visual_description": self.visual_description,
            "visible_text": self.visible_text,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class ModelCandidate:
    candidate_type: str
    title: str
    payload: dict[str, Any]
    validation_status: str = "invalid"
    validation_errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExtractionProcessResult:
    processed: int
    skipped: int
    errors: int
    windows: int
    artifacts: int
    visual_snippets: int
    candidates: int
    promotion_ready: int

    def to_stats(self) -> dict[str, int]:
        return asdict(self)

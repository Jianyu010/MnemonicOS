from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class SourceRef:
    source_kind: str
    archive_id: str | None = None
    conversation_id: str | None = None
    episode_id: str | None = None


@dataclass(slots=True)
class SourceSpan:
    source_kind: str
    source_locator: str
    char_start: int | None
    char_end: int | None


@dataclass(slots=True)
class ArtifactMarker:
    artifact_id: str
    kind: str
    marker_text: str
    char_offset: int


@dataclass(slots=True)
class ProjectionOptions:
    window_token_target: int = 10_000
    window_token_overlap: int = 512
    figures_enabled: bool = True
    max_per_figure_bytes: int = 256 * 1024 * 1024
    max_total_payload_bytes: int | None = None
    lease_idle_ttl_seconds: int = 1_800
    lease_hard_ttl_seconds: int = 21_600


@dataclass(slots=True)
class ProjectionLease:
    lease_id: str
    source_ref: SourceRef
    duplicate_state: str
    canonical_source_ref: SourceRef | None
    opened_at: datetime
    expires_at: datetime
    warnings: list[str]
    projection_metadata: dict[str, object]


@dataclass(slots=True)
class TextWindow:
    window_id: str
    text: str
    source_spans: list[SourceSpan]
    artifact_markers: list[ArtifactMarker]
    token_estimate: int


@dataclass(slots=True)
class ArtifactView:
    artifact_id: str
    kind: str
    mime_type: str | None
    payload: bytes | None
    source_attachment_path: str | None
    source_locator: str
    context_windows: list[str] = field(default_factory=list)
    marker: str = ""
    warnings: list[str] = field(default_factory=list)

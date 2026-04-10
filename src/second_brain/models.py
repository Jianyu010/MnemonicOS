from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class NoteRecord:
    id: str
    type: str
    title: str
    status: str | None
    confidence: str | None
    tags: list[str]
    entities: list[str]
    source_refs: list[str]
    valid_from: str | None
    valid_to: str | None
    updated_at: str
    created_at: str
    last_verified_at: str | None
    verified_by: str | None
    last_observed_at: str | None
    summary: str
    aliases: list[str]
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
    body_path: str
    summary: str
    channels: list[str]
    raw_score: float | None
    final_score: float
    excerpt: str


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
class JobRunResult:
    job_name: str
    status: str
    stats: dict[str, int]
    review_items_created: list[str]

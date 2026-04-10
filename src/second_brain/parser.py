from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

import yaml

from .models import NoteRecord


SUPPORTED_NOTE_TYPES = {
    "person",
    "project",
    "repo",
    "decision",
    "procedure",
    "concept",
    "incident",
    "source",
    "journal",
    "overview",
}

ENTITY_FIELDS = (
    "entities",
    "linked_projects",
    "linked_notes",
    "linked_procedures",
    "linked_decisions",
    "active_decisions",
    "owners",
)

FRONTMATTER_PATTERN = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", re.DOTALL)


class NoteParseError(ValueError):
    """Raised when a note cannot be parsed or validated."""


@dataclass(slots=True)
class ParsedFrontmatter:
    metadata: dict[str, object]
    body: str


def _ensure_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _collect_entities(metadata: dict[str, object]) -> list[str]:
    values: list[str] = []
    for field_name in ENTITY_FIELDS:
        values.extend(_ensure_list(metadata.get(field_name)))
    repo_value = metadata.get("repo")
    if repo_value:
        values.extend(_ensure_list(repo_value))
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def _extract_summary(metadata: dict[str, object], body: str) -> str:
    explicit = str(metadata.get("summary", "")).strip()
    if explicit:
        return explicit[:400]

    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- ") or line.startswith("* ") or re.match(r"^\d+\.\s+", line):
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))

    if paragraphs:
        return paragraphs[0][:400]
    return ""


def split_frontmatter(text: str) -> ParsedFrontmatter:
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        raise NoteParseError("missing YAML frontmatter")
    raw_metadata, body = match.groups()
    data = yaml.safe_load(raw_metadata) or {}
    if not isinstance(data, dict):
        raise NoteParseError("frontmatter must parse to a mapping")
    return ParsedFrontmatter(metadata=data, body=body.strip())


def parse_note(path: Path) -> NoteRecord:
    text = path.read_text()
    parsed = split_frontmatter(text)
    metadata = parsed.metadata

    note_type = str(metadata.get("type", "")).strip()
    note_id = str(metadata.get("id", "")).strip()
    title = str(metadata.get("title", "")).strip()
    updated_at = str(metadata.get("updated_at", "")).strip()
    created_at = str(metadata.get("created_at", "")).strip()

    missing_fields = [
        name
        for name, value in (
            ("id", note_id),
            ("type", note_type),
            ("title", title),
            ("updated_at", updated_at),
            ("created_at", created_at),
        )
        if not value
    ]
    if missing_fields:
        raise NoteParseError(f"missing required fields: {', '.join(missing_fields)}")
    if note_type not in SUPPORTED_NOTE_TYPES:
        raise NoteParseError(f"unsupported note type: {note_type}")
    if not note_id.startswith(f"{note_type}/"):
        raise NoteParseError(f"note id must start with '{note_type}/'")

    aliases = _ensure_list(metadata.get("aliases"))
    tags = _ensure_list(metadata.get("tags"))
    source_refs = _ensure_list(metadata.get("source_refs"))
    entities = _collect_entities(metadata)
    summary = _extract_summary(metadata, parsed.body)
    content_hash = sha256(text.encode("utf-8")).hexdigest()

    return NoteRecord(
        id=note_id,
        type=note_type,
        title=title,
        status=str(metadata.get("status", "")).strip() or None,
        confidence=str(metadata.get("confidence", "")).strip() or None,
        tags=tags,
        entities=entities,
        source_refs=source_refs,
        valid_from=str(metadata.get("valid_from", "")).strip() or None,
        valid_to=str(metadata.get("valid_to", "")).strip() or None,
        updated_at=updated_at,
        created_at=created_at,
        last_verified_at=str(metadata.get("last_verified_at", "")).strip() or None,
        verified_by=str(metadata.get("verified_by", "")).strip() or None,
        last_observed_at=str(metadata.get("last_observed_at", "")).strip() or None,
        summary=summary,
        aliases=aliases,
        body=parsed.body,
        body_path=path.resolve(),
        content_hash=content_hash,
    )

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any

import yaml

from .artifact_identity import (
    IdentityResult,
    classify_post_parse_identity,
    find_exact_duplicate,
    update_archive_identity,
    write_identity_buckets,
)
from .config import AppConfig
from .connectors import ConnectorConfig, connector_for_path, figure_from_bytes
from .db import connect_db, run_migrations
from .models import IngestArtifactResult, IngestSessionResult
from .review import slugify
from .semantics import encode_text, upsert_archive_chunk_vector
from .summaries import summarize_text


MEMORY_BLOCK_PATTERN = re.compile(r"```(?:memory|mnemonic)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
INLINE_ITEM_PATTERN = re.compile(
    r"^\s*(Decision|Procedure|Incident|Concept|Project|Repo|Person|Source|Preference|Convention):\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
WORD_PATTERN = re.compile(r"\S+")
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]

# Patterns for session content extraction (options A and B)
_NUANCE_LINE_RE = re.compile(r"^Nuance:\s*(.+?)$", re.MULTILINE)
_NEXT_LINE_RE = re.compile(r"^(?:Next|Next Time):\s*(.+?)$", re.MULTILINE)
_WHAT_LINE_RE = re.compile(r"^(?:What|What Happened):\s*(.+?)$", re.MULTILINE)
_TAKEAWAY_SECTION_RE = re.compile(
    r"##\s+Reusable Takeaways\s*\n(.*?)(?=\n##|\Z)", re.DOTALL
)
_TAKEAWAY_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)$", re.MULTILINE)
_ERROR_TOKEN_RE = re.compile(
    r"\b[A-Z][a-zA-Z]*(?:Error|Exception|Warning|Timeout)\b"
    r"|\bexit\s+[012]\b"
    r"|\bTIMEOUT\b"
    r"|\bNEEDS_REVISION\b",
)


def _migrations_dir(config: AppConfig) -> Path:
    configured = config.paths.workspace_root / "migrations"
    return configured if configured.exists() else _PACKAGE_ROOT / "migrations"


def _synthesize_summary(item: dict[str, Any], item_context: str) -> str:
    """Build a compact, vocabulary-rich summary if item lacks one.

    Priority:
    1. Item already has a non-title summary  → keep it.
    2. Nuance: + Next: lines              → compact control signal.
    3. First Reusable Takeaway bullet     → use verbatim.
    4. What: line                         → use as situation summary.
    5. Procedure with steps               → gene template from steps.
    6. Incident with symptom/fix          → error-aware situation/fix sentence.
    7. Next: line                         → use verbatim.
    8. Return empty string (let caller fall through to summarize_text).
    """
    title = str(item.get("title", "")).strip()
    existing = str(item.get("summary", "")).strip()
    if existing and existing.casefold() != title.casefold():
        return existing

    item_type = str(item.get("type", "")).casefold()

    nuance = _line_value(_NUANCE_LINE_RE, item_context)
    next_line = _line_value(_NEXT_LINE_RE, item_context)
    if nuance:
        if next_line and not _looks_like_control_signal(nuance):
            return f"WHEN {nuance} → {next_line}"
        return nuance

    takeaway_section = _TAKEAWAY_SECTION_RE.search(item_context)
    if takeaway_section:
        bullet = _TAKEAWAY_BULLET_RE.search(takeaway_section.group(1))
        if bullet:
            return bullet.group(1).strip()

    what_line = _line_value(_WHAT_LINE_RE, item_context)
    if what_line:
        return what_line

    if item_type == "procedure":
        steps = item.get("steps") or []
        non_avoid = [s for s in steps if not str(s).strip().lower().startswith("avoid:")]
        avoid = [s for s in steps if str(s).strip().lower().startswith("avoid:")]
        if non_avoid:
            do_clause = str(non_avoid[0]).strip()
            not_clause = str(avoid[0]).replace("Avoid:", "").replace("avoid:", "").strip() if avoid else ""
            return f"{do_clause} (NOT: {not_clause})" if not_clause else do_clause

    if item_type == "incident":
        fix = str(item.get("fix", "")).strip()
        symptom = str(item.get("symptom", "")).strip()
        if symptom and fix and not _is_placeholder_signal(fix):
            return f"WHEN {symptom[:160]} → {fix[:160]}"
        if symptom and 10 < len(symptom) <= 280:
            return f"WHEN {symptom}"
        if fix and 10 < len(fix) <= 280 and not _is_placeholder_signal(fix):
            return f"Fix: {fix}"

    if next_line:
        return next_line

    return ""


def _line_value(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _looks_like_control_signal(text: str) -> bool:
    return "→" in text or "->" in text or text.lstrip().upper().startswith("WHEN ")


def _is_placeholder_signal(text: str) -> bool:
    lowered = " ".join(text.casefold().split())
    return (
        "needs_revision" in lowered
        or lowered in {"the phase 3 milestone", "the phase milestone"}
        or lowered.startswith("[needs_revision]")
    )


def _item_context_text(item: dict[str, Any]) -> str:
    fields = [
        str(item.get("title", "")),
        str(item.get("summary", "")),
        str(item.get("body", "")),
        str(item.get("symptom", "")),
        str(item.get("fix", "")),
    ]
    for step in (item.get("steps") or []):
        fields.append(str(step))
    for mode in (item.get("failure_modes") or []):
        if isinstance(mode, dict):
            fields.extend(str(value) for value in mode.values())
        else:
            fields.append(str(mode))
    return "\n".join(field for field in fields if field)


def _extract_error_aliases(item: dict[str, Any], item_context: str = "") -> list[str]:
    """Extract error/exception class names from item fields for alias injection.

    Scans only item-level fields so one promoted memory item cannot inherit
    error tokens from unrelated notes in the same session.
    """
    combined = item_context or _item_context_text(item)
    return list(dict.fromkeys(_ERROR_TOKEN_RE.findall(combined)))

TYPE_TO_DIRECTORY = {
    "person": "people",
    "project": "projects",
    "repo": "repos",
    "decision": "decisions",
    "concept": "concepts",
    "incident": "incidents",
    "source": "sources",
    "overview": "overviews",
    "journal": "journals",
}

LIFECYCLE_DEFAULTS = {
    "decision": "canonical",
    "concept": "canonical",
    "source": "verified",
    "procedure": "draft",
    "incident": "review",
    "project": "canonical",
    "repo": "canonical",
    "person": "verified",
    "overview": "canonical",
    "journal": "archived",
}

RELATION_BUCKETS = {
    "before": "timeline",
    "after": "timeline",
    "supersedes": "timeline",
    "similar_to": "similarity",
    "contrasts_with": "contrast",
}


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        normalized = str(item).strip()
        if not normalized:
            continue
        lowered = normalized.casefold()
        if lowered in seen:
            continue
        ordered.append(normalized)
        seen.add(lowered)
    return ordered


CHAIN_BOUNDED_TYPES = {"procedure", "incident", "decision", "concept"}


def _read_memory_items(content: str, explicit_only: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for block in MEMORY_BLOCK_PATTERN.findall(content):
        parsed = yaml.safe_load(block) or []
        if isinstance(parsed, dict) and "items" in parsed:
            parsed = parsed["items"]
        if isinstance(parsed, dict):
            parsed = [parsed]
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    items.append(dict(item))

    if explicit_only:
        return items

    for raw_type, raw_title in INLINE_ITEM_PATTERN.findall(content):
        item_type = raw_type.casefold()
        title = raw_title.strip()
        items.append(
            {
                "type": item_type,
                "title": title,
                "summary": title,
                "confidence": "medium",
            }
        )

    return items


def _chunk_text(text: str, *, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    words = WORD_PATTERN.findall(text)
    if not words:
        return []
    chunks: list[str] = []
    step = max(chunk_tokens - overlap_tokens, 1)
    for start in range(0, len(words), step):
        chunk_words = words[start : start + chunk_tokens]
        if not chunk_words:
            continue
        chunks.append(" ".join(chunk_words))
        if start + chunk_tokens >= len(words):
            break
    return chunks


def _upsert_archive_sync_state(connection: sqlite3.Connection, path: Path, archive_id: str, content_hash: str) -> None:
    connection.execute(
        """
        INSERT INTO sync_state(path, kind, note_id, content_hash, last_synced_at, parse_status, last_error)
        VALUES (?, 'archive', ?, ?, datetime('now'), 'ok', NULL)
        ON CONFLICT(path) DO UPDATE SET
            note_id = excluded.note_id,
            content_hash = excluded.content_hash,
            last_synced_at = excluded.last_synced_at,
            parse_status = excluded.parse_status,
            last_error = excluded.last_error
        """,
        (str(path.resolve()), archive_id, content_hash),
    )


def _render_note(metadata: dict[str, Any], body: str) -> str:
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"


def _all_relation_targets(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    relation_targets: list[tuple[str, str]] = []
    for field_name, relation_type in (
        ("entities", "entity"),
        ("linked_projects", "linked_project"),
        ("linked_notes", "linked_note"),
        ("linked_procedures", "linked_procedure"),
        ("linked_decisions", "linked_decision"),
        ("active_decisions", "active_decision"),
        ("owners", "owner"),
        ("repo", "repo"),
    ):
        values = metadata.get(field_name) or []
        if not isinstance(values, list):
            values = [values]
        for value in values:
            normalized = str(value).strip()
            if normalized:
                relation_targets.append((normalized, relation_type))
    relations = metadata.get("relations") or []
    if isinstance(relations, list):
        for entry in relations:
            if not isinstance(entry, dict):
                continue
            relation_type = str(entry.get("type", "")).strip()
            target = str(entry.get("target", "")).strip()
            if relation_type and target:
                relation_targets.append((target, relation_type))
    return relation_targets


def _relative_note_link(config: AppConfig, target_id: str) -> str | None:
    if "/" not in target_id:
        return None
    target_type, slug = target_id.split("/", 1)
    if target_type == "procedure":
        procedure_root = config.paths.vault_root / "wiki" / "procedures"
        for directory in ("active", "drafts", "retired"):
            candidate = procedure_root / directory / f"{slug}.md"
            if candidate.exists():
                relative = candidate.relative_to(config.paths.vault_root)
                return f"[[{relative.as_posix()[:-3]}]]"
        return f"[[wiki/procedures/active/{slug}]]"
    if target_type not in TYPE_TO_DIRECTORY:
        return None
    relative_path = Path("wiki") / TYPE_TO_DIRECTORY[target_type] / f"{slug}.md"
    return f"[[{relative_path.as_posix()[:-3]}]]"


def _project_obsidian_links(config: AppConfig, metadata: dict[str, Any]) -> dict[str, Any]:
    projected = dict(metadata)
    topic_links: list[str] = []
    timeline_links: list[str] = []
    similarity_links: list[str] = []
    contrast_links: list[str] = []
    all_links: list[str] = []

    for target_id, relation_type in _all_relation_targets(projected):
        link = _relative_note_link(config, target_id)
        if not link:
            continue
        all_links = _merge_unique(all_links, [link])
        bucket = RELATION_BUCKETS.get(relation_type, "topic")
        if bucket == "timeline":
            timeline_links = _merge_unique(timeline_links, [link])
        elif bucket == "similarity":
            similarity_links = _merge_unique(similarity_links, [link])
        elif bucket == "contrast":
            contrast_links = _merge_unique(contrast_links, [link])
        else:
            topic_links = _merge_unique(topic_links, [link])

    if all_links:
        projected["obsidian_links"] = all_links
    if topic_links:
        projected["obsidian_topic_links"] = topic_links
    if timeline_links:
        projected["obsidian_timeline_links"] = timeline_links
    if similarity_links:
        projected["obsidian_similarity_links"] = similarity_links
    if contrast_links:
        projected["obsidian_contrast_links"] = contrast_links
    return projected


def _render_note_body(item_type: str, item: dict[str, Any]) -> str:
    summary = str(item.get("summary", "")).strip()
    body = str(item.get("body", "")).strip()
    if item_type == "decision":
        sections = ["## Decision", "", body or summary or item["title"]]
        rationale = str(item.get("rationale", "")).strip()
        if rationale:
            sections.extend(["", "## Rationale", "", rationale])
        alternatives = item.get("alternatives_considered") or []
        if isinstance(alternatives, list) and alternatives:
            sections.extend(["", "## Alternatives Considered", ""])
            sections.extend(f"- {entry}" for entry in alternatives)
        return "\n".join(sections).strip()

    if item_type == "procedure":
        steps = item.get("steps") or []
        failure_modes = item.get("failure_modes") or []
        sections: list[str] = []
        if failure_modes:
            sections.extend(["## Failure Modes", ""])
            for mode in failure_modes:
                if isinstance(mode, dict):
                    sections.append(f"- symptom: {mode.get('symptom', '')}")
                    sections.append(f"  cause: {mode.get('cause', '')}")
                    sections.append(f"  fix: {mode.get('fix', '')}")
                else:
                    sections.append(f"- {mode}")
            sections.append("")
        sections.extend(["## Steps", ""])
        if isinstance(steps, list) and steps:
            sections.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
        elif body:
            sections.append(body)
        else:
            sections.append("1. Fill in the procedure steps.")
        return "\n".join(sections).strip()

    if item_type == "incident":
        symptom = str(item.get("symptom", "")).strip()
        cause = str(item.get("cause", "")).strip()
        fix = str(item.get("fix", "")).strip()
        prevention = item.get("prevention") or []
        sections = ["## Symptom", "", symptom or summary or item["title"], "", "## Cause", "", cause or ""]
        sections.extend(["", "## Fix", "", fix or ""])
        if prevention:
            sections.extend(["", "## Prevention", ""])
            sections.extend(f"- {entry}" for entry in prevention)
        return "\n".join(sections).strip()

    if item_type == "source":
        return f"## Summary\n\n{body or summary or item['title']}"

    if item_type in {"concept", "person", "project", "repo"}:
        return body or summary or item["title"]

    return body or summary or item["title"]


def _normalize_item(
    item: dict[str, Any],
    *,
    archive_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    item_type = str(item.get("type", "")).strip().casefold()
    if not item_type:
        return None

    title = str(item.get("title", "")).strip()
    summary = str(item.get("summary", "")).strip()
    body = str(item.get("body", "")).strip()
    confidence = str(item.get("confidence", "medium")).strip().casefold() or "medium"
    aliases = item.get("aliases") or []
    tags = item.get("tags") or []
    source_refs = _merge_unique([archive_id], list(item.get("source_refs") or []))
    now_date = now.strftime("%Y-%m-%d")

    if item_type in {"preference", "convention"} and not title:
        title = body or summary
    if not title and item_type not in {"preference", "convention"}:
        return None

    if item_type in {"person", "project", "repo", "decision", "procedure", "concept", "incident", "source", "overview", "journal"}:
        note_id = str(item.get("id", "")).strip() or f"{item_type}/{slugify(title)}"
        normalized = {
            "type": item_type,
            "id": note_id,
            "title": title,
            "summary": summary or summarize_text(body or title),
            "body": body,
            "aliases": list(aliases) if isinstance(aliases, list) else [str(aliases)],
            "tags": list(tags) if isinstance(tags, list) else [str(tags)],
            "source_refs": source_refs,
            "confidence": confidence,
            "updated_at": str(item.get("updated_at", now_date)),
            "created_at": str(item.get("created_at", now_date)),
            "overlay_class": str(item.get("overlay_class", "")).strip() or None,
            "lifecycle_state": str(item.get("lifecycle_state", "")).strip() or LIFECYCLE_DEFAULTS.get(item_type),
        }
        for field in (
            "status",
            "entities",
            "owners",
            "valid_from",
            "valid_to",
            "review_date",
            "reviewed_by",
            "reviewed_at",
            "last_verified_at",
            "verified_by",
            "chain_id",
            "chain_confidence",
            "chain_question",
            "chain_summary",
            "problem_class",
            "failure_summary",
            "attempt_summaries",
            "solution_rationale",
            "evidence_refs",
            "verification",
            "applicability",
            "severity",
            "linked_procedures",
            "linked_decisions",
            "linked_notes",
            "linked_projects",
            "repo",
            "stack",
            "purpose",
            "org",
            "role",
            "key_extractions",
            "entities_mentioned",
            "failure_modes",
            "steps",
            "rationale",
            "alternatives_considered",
            "symptom",
            "cause",
            "fix",
            "prevention",
            "origin",
            "ingested_at",
            "relations",
        ):
            if field in item:
                normalized[field] = item[field]
        return normalized

    if item_type in {"preference", "convention"}:
        return {
            "type": item_type,
            "title": title,
            "summary": summary or title,
            "confidence": confidence,
            "source_refs": source_refs,
        }

    return None


def _path_for_item(config: AppConfig, item: dict[str, Any]) -> Path:
    item_type = item["type"]
    slug = item["id"].split("/", 1)[1]
    if item_type == "procedure":
        status = str(item.get("status", "draft")).strip() or "draft"
        directory = "active" if status == "active" else "retired" if status == "retired" else "drafts"
        return config.paths.vault_root / "wiki" / "procedures" / directory / f"{slug}.md"
    directory = TYPE_TO_DIRECTORY[item_type]
    return config.paths.vault_root / "wiki" / directory / f"{slug}.md"


def _merge_note_metadata(existing_metadata: dict[str, Any], item: dict[str, Any], *, now_date: str) -> dict[str, Any]:
    metadata = dict(existing_metadata)
    metadata["id"] = item["id"]
    metadata["type"] = item["type"]
    existing_title = str(existing_metadata.get("title", "")).strip()
    incoming_title = str(item["title"]).strip()
    if existing_title:
        metadata["title"] = existing_title
        if incoming_title and incoming_title.casefold() != existing_title.casefold():
            metadata["aliases"] = _merge_unique(
                list(existing_metadata.get("aliases") or []),
                [incoming_title, *list(item.get("aliases") or [])],
            )
        else:
            metadata["aliases"] = _merge_unique(
                list(existing_metadata.get("aliases") or []),
                list(item.get("aliases") or []),
            )
    else:
        metadata["title"] = incoming_title
        metadata["aliases"] = _merge_unique(
            list(existing_metadata.get("aliases") or []),
            list(item.get("aliases") or []),
        )
    metadata["tags"] = _merge_unique(
        list(existing_metadata.get("tags") or []),
        list(item.get("tags") or []),
    )
    metadata["source_refs"] = _merge_unique(
        list(existing_metadata.get("source_refs") or []),
        list(item.get("source_refs") or []),
    )
    metadata["confidence"] = item.get("confidence") or existing_metadata.get("confidence", "medium")
    metadata["updated_at"] = now_date
    metadata["last_observed_at"] = now_date
    metadata["created_at"] = str(existing_metadata.get("created_at", item.get("created_at", now_date)))
    for key, value in item.items():
        if key in {"aliases", "tags", "source_refs", "updated_at", "created_at", "title", "summary", "body"}:
            continue
        if value in (None, "", []):
            continue
        metadata[key] = value
    if "summary" in item and item["summary"]:
        metadata["summary"] = item["summary"]
    return metadata


def _same_chain_candidate(
    connection: sqlite3.Connection,
    *,
    note_type: str,
    chain_id: str | None,
) -> str | None:
    if not chain_id:
        return None
    row = connection.execute(
        """
        SELECT id
        FROM notes
        WHERE type = ? AND chain_id = ?
        ORDER BY updated_at DESC, created_at DESC, id ASC
        LIMIT 1
        """,
        (note_type, chain_id),
    ).fetchone()
    return str(row["id"]) if row is not None else None


def _touch_existing_note(path: Path, source_refs: list[str], now_date: str) -> bool:
    from .parser import split_frontmatter

    if not path.exists():
        return False
    parsed = split_frontmatter(path.read_text())
    metadata = dict(parsed.metadata)
    metadata["source_refs"] = _merge_unique(list(metadata.get("source_refs") or []), source_refs)
    metadata["last_observed_at"] = now_date
    rendered = _render_note(metadata, parsed.body)
    path.write_text(rendered)
    return True


def _apply_note_defaults(metadata: dict[str, Any], normalized_type: str) -> dict[str, Any]:
    if normalized_type == "procedure":
        metadata.setdefault("status", "active" if metadata.get("reviewed_by") and metadata.get("reviewed_at") else "draft")
        if metadata["status"] == "active" and not (metadata.get("reviewed_by") and metadata.get("reviewed_at")):
            metadata["status"] = "draft"
    elif normalized_type == "decision":
        metadata.setdefault("status", "active")
        metadata.setdefault("valid_from", metadata.get("created_at"))
    elif normalized_type == "incident":
        metadata.setdefault("status", "open")
    elif normalized_type in {"project", "repo"}:
        metadata.setdefault("status", "active")
    metadata.setdefault("lifecycle_state", LIFECYCLE_DEFAULTS.get(normalized_type))
    return metadata


def _append_system_memory(path: Path, heading: str, bullet: str) -> bool:
    lines = path.read_text().splitlines()
    normalized_bullet = f"- {bullet.strip()}"
    if any(line.strip() == normalized_bullet for line in lines):
        return False

    heading_line = f"## {heading}"
    for index, line in enumerate(lines):
        if line.strip() != heading_line:
            continue
        insert_at = index + 1
        while insert_at < len(lines) and (not lines[insert_at].startswith("## ")):
            insert_at += 1
        lines.insert(insert_at, normalized_bullet)
        path.write_text("\n".join(lines).rstrip() + "\n")
        return True

    if lines and lines[-1].strip():
        lines.append("")
    lines.extend([heading_line, normalized_bullet])
    path.write_text("\n".join(lines).rstrip() + "\n")
    return True


def _archive_session(
    config: AppConfig,
    *,
    resolved_bridge_id: str,
    resolved_session_id: str,
    content: str,
    tags: list[str] | None,
    skip_vectors: bool,
) -> tuple[str, Path, list[str], sqlite3.Connection]:
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d-%H%M%S")
    archive_name = f"{timestamp}-{slugify(resolved_bridge_id)}-{slugify(resolved_session_id)}"
    archive_id = f"session/{archive_name}"
    raw_path = config.paths.vault_root / "raw" / "sessions" / f"{archive_name}.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(content.rstrip() + "\n")

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))

    chunks = _chunk_text(
        content,
        chunk_tokens=config.ingest.chunk_tokens,
        overlap_tokens=config.ingest.chunk_overlap_tokens,
    )

    with connection:
        connection.execute(
            """
            INSERT INTO archive(id, type, path, ingested_at, agent, tags, chunk_count)
            VALUES (?, ?, ?, datetime('now'), ?, ?, ?)
            """,
            (
                archive_id,
                config.ingest.default_archive_type,
                str(raw_path.resolve()),
                resolved_bridge_id,
                json.dumps(tags or []),
                len(chunks),
            ),
        )
        raw_hash = sha256(raw_path.read_bytes()).hexdigest()
        _upsert_archive_sync_state(connection, raw_path, archive_id, raw_hash)

        for index, chunk in enumerate(chunks):
            summary = summarize_text(chunk, max_sentences=2, max_chars=240)
            content_hash = sha256(chunk.encode("utf-8")).hexdigest()
            cursor = connection.execute(
                """
                INSERT INTO archive_chunks(archive_id, chunk_index, body, summary, token_count, content_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (archive_id, index, chunk, summary, len(WORD_PATTERN.findall(chunk)), content_hash),
            )
            chunk_id = int(cursor.lastrowid)
            if not skip_vectors and config.embeddings.enabled:
                vector = encode_text(summary or chunk, dimensions=config.embeddings.dimensions)
                upsert_archive_chunk_vector(
                    connection,
                    chunk_id=chunk_id,
                    model=config.embeddings.model,
                    dimensions=config.embeddings.dimensions,
                    vector=vector,
                    source_hash=content_hash,
                )
    return archive_id, raw_path, chunks, connection


def _jsonable_figure(figure: Any) -> dict[str, Any]:
    payload = figure.to_dict()
    warnings = payload.pop("warnings", [])
    payload["warnings_json"] = list(warnings or [])
    return payload


def _jsonable_warnings(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return [str(value)]


def _artifact_archive_type(source_format: str) -> str:
    return "web_clip" if source_format == "html" else "doc"


def ingest_artifact(
    config: AppConfig,
    *,
    path: str | Path,
    tags: list[str] | None = None,
    attachments: list[str | Path] | None = None,
    connector_config: ConnectorConfig | None = None,
) -> IngestArtifactResult:
    source_path = Path(path).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(str(source_path))

    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d-%H%M%S-%f")
    source_slug = slugify(source_path.stem) or "artifact"
    archive_name = f"{timestamp}-{source_slug}"
    archive_id = f"artifact/{archive_name}"
    artifact_dir = config.paths.vault_root / "raw" / "artifacts" / archive_name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"original{source_path.suffix.casefold()}"
    shutil.copy2(source_path, raw_path)

    raw_bytes = raw_path.read_bytes()
    raw_hash = sha256(raw_bytes).hexdigest()

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    try:
        duplicate = find_exact_duplicate(connection, raw_sha256=raw_hash, byte_size=len(raw_bytes))
    finally:
        connection.close()
    if duplicate is not None:
        canonical_archive_id = str(duplicate["id"])
        if raw_path.exists():
            raw_path.unlink()
        source_format = str(duplicate["source_format"] or source_path.suffix.casefold().lstrip(".") or "unknown")
        warnings = [f"exact_duplicate_of:{canonical_archive_id}"]
        manifest_path = artifact_dir / "manifest.json"
        manifest = {
            "archive_id": archive_id,
            "artifact_path": str(duplicate["artifact_path"] or duplicate["path"]),
            "source_format": source_format,
            "original_filename": source_path.name,
            "raw_sha256": None,
            "canonical_raw_sha256": raw_hash,
            "byte_size": len(raw_bytes),
            "connector_name": str(duplicate["connector_name"] or ""),
            "connector_version": str(duplicate["connector_version"] or ""),
            "ingest_status": "accepted",
            "duplicate_state": "exact_duplicate",
            "canonical_archive_id": canonical_archive_id,
            "pre_parse_short_circuited": True,
            "page_count": int(duplicate["page_count"] or 0),
            "slide_count": int(duplicate["slide_count"] or 0),
            "figure_count": int(duplicate["figure_count"] or 0),
            "table_count": int(duplicate["table_count"] or 0),
            "warnings": warnings,
            "failure_reason": None,
            "needs_visual_pass": bool(duplicate["needs_visual_pass"]),
            "needs_manual_projection": bool(duplicate["needs_manual_projection"]),
            "image_only_pdf": bool(duplicate["image_only_pdf"]),
            "figures": [],
            "ingested_at": now.isoformat(timespec="seconds"),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, _migrations_dir(config))
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO archive(
                        id, type, path, ingested_at, agent, tags, chunk_count,
                        artifact_path, source_format, original_filename, raw_sha256, byte_size,
                        connector_name, connector_version, ingest_status, page_count, slide_count,
                        figure_count, table_count, warnings_json, failure_reason, needs_visual_pass,
                        needs_manual_projection, image_only_pdf, duplicate_state, canonical_archive_id,
                        duplicate_confidence, overlap_summary_json
                    )
                    VALUES (
                        ?, ?, ?, datetime('now'), NULL, ?, 0,
                        ?, ?, ?, NULL, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, NULL, ?,
                        ?, ?, 'exact_duplicate', ?,
                        1.0, '{}'
                    )
                    """,
                    (
                        archive_id,
                        _artifact_archive_type(source_format),
                        str(manifest_path.resolve()),
                        json.dumps(tags or []),
                        str(duplicate["artifact_path"] or duplicate["path"]),
                        source_format,
                        source_path.name,
                        len(raw_bytes),
                        manifest["connector_name"],
                        manifest["connector_version"],
                        "accepted",
                        manifest["page_count"],
                        manifest["slide_count"],
                        manifest["figure_count"],
                        manifest["table_count"],
                        json.dumps(warnings),
                        1 if manifest["needs_visual_pass"] else 0,
                        1 if manifest["needs_manual_projection"] else 0,
                        1 if manifest["image_only_pdf"] else 0,
                        canonical_archive_id,
                    ),
                )
                _upsert_archive_sync_state(connection, manifest_path, archive_id, raw_hash)
        finally:
            connection.close()
        return IngestArtifactResult(
            archive_id=archive_id,
            raw_path=str(duplicate["artifact_path"] or duplicate["path"]),
            manifest_path=str(manifest_path.resolve()),
            ingest_status="accepted",
            source_format=source_format,
            figure_count=manifest["figure_count"],
            warnings=warnings,
            failure_reason=None,
            needs_visual_pass=manifest["needs_visual_pass"],
            needs_manual_projection=manifest["needs_manual_projection"],
            image_only_pdf=manifest["image_only_pdf"],
            duplicate_state="exact_duplicate",
            canonical_archive_id=canonical_archive_id,
        )

    connector = connector_for_path(raw_path)
    result = connector.extract(raw_path, config=connector_config or ConnectorConfig())
    metadata = dict(result.metadata)
    source_format = str(metadata.get("source_format") or getattr(connector, "source_format", "unknown"))
    warnings = list(result.warnings)
    figure_rows = [_jsonable_figure(figure) for figure in result.figures]
    source_attachments = [Path(item).expanduser().resolve() for item in (attachments or [])]
    if source_attachments:
        attachments_dir = artifact_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        start_index = len(figure_rows) + 1
        for offset, attachment_source in enumerate(source_attachments, start=start_index):
            if not attachment_source.exists() or not attachment_source.is_file():
                warnings.append(f"attachment_missing:{attachment_source}")
                continue
            target = attachments_dir / attachment_source.name
            shutil.copy2(attachment_source, target)
            data = target.read_bytes()
            figure = figure_from_bytes(
                figure_id=f"fig_{offset:03d}",
                data=data,
                storage_policy="source_attachment",
                attachment_path=str(target.resolve()),
                source_index=offset,
                structural_location=f"attachment:{attachment_source.name}",
            )
            figure_rows.append(_jsonable_figure(figure))
    ingest_status = result.status
    if ingest_status not in {"accepted", "accepted_with_warnings", "failed_extraction", "manual_fix_needed"}:
        ingest_status = "failed_extraction"
        warnings.append(f"invalid_connector_status:{result.status}")
    failure_reason = result.failure_reason
    needs_manual_projection = bool(metadata.get("needs_manual_projection")) or ingest_status == "failed_extraction"
    needs_visual_pass = bool(metadata.get("needs_visual_pass")) or any(bool(row.get("needs_visual_pass")) for row in figure_rows)
    image_only_pdf = bool(metadata.get("image_only_pdf"))
    figure_count = max(int(metadata.get("figure_count") or 0), len(figure_rows))
    table_count = int(metadata.get("table_count") or 0)
    page_count = int(metadata.get("page_count") or 0)
    slide_count = int(metadata.get("slide_count") or 0)

    manifest = {
        "archive_id": archive_id,
        "artifact_path": str(raw_path.resolve()),
        "source_format": source_format,
        "original_filename": source_path.name,
        "raw_sha256": raw_hash,
        "byte_size": len(raw_bytes),
        "connector_name": str(metadata.get("connector_name") or getattr(connector, "name", "unknown_connector")),
        "connector_version": str(metadata.get("connector_version") or ""),
        "ingest_status": ingest_status,
        "page_count": page_count,
        "slide_count": slide_count,
        "figure_count": figure_count,
        "table_count": table_count,
        "warnings": warnings,
        "failure_reason": failure_reason,
        "needs_visual_pass": needs_visual_pass,
        "needs_manual_projection": needs_manual_projection,
        "image_only_pdf": image_only_pdf,
        "figures": figure_rows,
        "ingested_at": now.isoformat(timespec="seconds"),
    }
    manifest_path = artifact_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO archive(
                    id, type, path, ingested_at, agent, tags, chunk_count,
                    artifact_path, source_format, original_filename, raw_sha256, byte_size,
                    connector_name, connector_version, ingest_status, page_count, slide_count,
                    figure_count, table_count, warnings_json, failure_reason, needs_visual_pass,
                    needs_manual_projection, image_only_pdf
                )
                VALUES (
                    ?, ?, ?, datetime('now'), NULL, ?, 0,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?
                )
                """,
                (
                    archive_id,
                    _artifact_archive_type(source_format),
                    str(raw_path.resolve()),
                    json.dumps(tags or []),
                    str(raw_path.resolve()),
                    source_format,
                    source_path.name,
                    raw_hash,
                    len(raw_bytes),
                    manifest["connector_name"],
                    manifest["connector_version"],
                    ingest_status,
                    page_count,
                    slide_count,
                    figure_count,
                    table_count,
                    json.dumps(warnings),
                    failure_reason,
                    1 if needs_visual_pass else 0,
                    1 if needs_manual_projection else 0,
                    1 if image_only_pdf else 0,
                ),
            )
            _upsert_archive_sync_state(connection, raw_path, archive_id, raw_hash)
            for row in figure_rows:
                connection.execute(
                    """
                    INSERT INTO artifact_figures(
                        archive_id, figure_id, storage_policy, attachment_path,
                        source_page, source_slide, source_index, width_px, height_px,
                        mime_type, byte_size, sha256, structural_location,
                        has_caption, referenced_by_label, needs_visual_pass, warnings_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        archive_id,
                        row.get("figure_id"),
                        row.get("storage_policy") or "in_memory_extracted",
                        row.get("attachment_path"),
                        row.get("source_page"),
                        row.get("source_slide"),
                        row.get("source_index"),
                        row.get("width_px"),
                        row.get("height_px"),
                        row.get("mime_type"),
                        row.get("byte_size"),
                        row.get("sha256"),
                        row.get("structural_location"),
                        1 if row.get("has_caption") else 0,
                        1 if row.get("referenced_by_label") else 0,
                        1 if row.get("needs_visual_pass") else 0,
                        json.dumps(row.get("warnings_json") or []),
                    ),
                )
            identity = classify_post_parse_identity(
                connection,
                archive_id=archive_id,
                source_format=source_format,
                usable_raw_text=result.usable_raw_text,
            )
            update_archive_identity(connection, archive_id=archive_id, result=identity)
            write_identity_buckets(
                connection,
                archive_id=archive_id,
                usable_raw_text=result.usable_raw_text,
                duplicate_state=identity.duplicate_state,
            )
    finally:
        connection.close()

    manifest["duplicate_state"] = identity.duplicate_state
    manifest["canonical_archive_id"] = identity.canonical_archive_id
    manifest["identity_fingerprint_version"] = identity.identity_fingerprint_version
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    return IngestArtifactResult(
        archive_id=archive_id,
        raw_path=str(raw_path.resolve()),
        manifest_path=str(manifest_path.resolve()),
        ingest_status=ingest_status,
        source_format=source_format,
        figure_count=figure_count,
        warnings=warnings,
        failure_reason=failure_reason,
        needs_visual_pass=needs_visual_pass,
        needs_manual_projection=needs_manual_projection,
        image_only_pdf=image_only_pdf,
        duplicate_state=identity.duplicate_state,
        canonical_archive_id=identity.canonical_archive_id,
    )


def _figure_rows_from_source_attachments(rows: list[sqlite3.Row], *, start_index: int, warnings: list[str]) -> list[dict[str, Any]]:
    figure_rows: list[dict[str, Any]] = []
    for offset, row in enumerate(rows, start=start_index):
        attachment_path = str(row["attachment_path"] or "")
        path = Path(attachment_path)
        if not attachment_path or not path.exists():
            warnings.append(f"attachment_missing:{attachment_path or row['figure_id']}")
            continue
        data = path.read_bytes()
        figure = figure_from_bytes(
            figure_id=f"fig_{offset:03d}",
            data=data,
            storage_policy="source_attachment",
            attachment_path=str(path.resolve()),
            source_index=offset,
            structural_location=str(row["structural_location"] or f"attachment:{path.name}"),
            mime_type=str(row["mime_type"] or "") or None,
        )
        figure_rows.append(_jsonable_figure(figure))
    return figure_rows


def _insert_artifact_figure_rows(connection: sqlite3.Connection, archive_id: str, figure_rows: list[dict[str, Any]]) -> None:
    for row in figure_rows:
        connection.execute(
            """
            INSERT INTO artifact_figures(
                archive_id, figure_id, storage_policy, attachment_path,
                source_page, source_slide, source_index, width_px, height_px,
                mime_type, byte_size, sha256, structural_location,
                has_caption, referenced_by_label, needs_visual_pass, warnings_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                archive_id,
                row.get("figure_id"),
                row.get("storage_policy") or "in_memory_extracted",
                row.get("attachment_path"),
                row.get("source_page"),
                row.get("source_slide"),
                row.get("source_index"),
                row.get("width_px"),
                row.get("height_px"),
                row.get("mime_type"),
                row.get("byte_size"),
                row.get("sha256"),
                row.get("structural_location"),
                1 if row.get("has_caption") else 0,
                1 if row.get("referenced_by_label") else 0,
                1 if row.get("needs_visual_pass") else 0,
                json.dumps(row.get("warnings_json") or []),
            ),
        )


def reprocess_artifact(
    config: AppConfig,
    *,
    archive_id: str,
    connector_config: ConnectorConfig | None = None,
) -> IngestArtifactResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    try:
        row = connection.execute(
            """
            SELECT id, path, artifact_path, original_filename, tags,
                   duplicate_state, canonical_archive_id
            FROM archive
            WHERE id = ? AND id LIKE 'artifact/%'
            """,
            (archive_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"artifact archive not found: {archive_id}")
        duplicate_state = str(row["duplicate_state"] or "unique")
        canonical_archive_id = row["canonical_archive_id"]
        if duplicate_state in {"exact_duplicate", "canonical_duplicate"} and canonical_archive_id:
            canonical_id = str(canonical_archive_id)
            canonical = connection.execute(
                """
                SELECT id, path, artifact_path, source_format, ingest_status, figure_count,
                       warnings_json, failure_reason, needs_visual_pass,
                       needs_manual_projection, image_only_pdf, duplicate_state,
                       canonical_archive_id
                FROM archive
                WHERE id = ?
                """,
                (canonical_id,),
            ).fetchone()
            if canonical is None:
                raise ValueError(f"canonical artifact archive not found: {canonical_id}")
            raw = str(canonical["artifact_path"] or canonical["path"])
            manifest_path = str((Path(raw).parent / "manifest.json").resolve())
            return IngestArtifactResult(
                archive_id=str(row["id"]),
                raw_path=raw,
                manifest_path=manifest_path,
                ingest_status=str(canonical["ingest_status"] or "accepted"),
                source_format=str(canonical["source_format"] or "unknown"),
                figure_count=int(canonical["figure_count"] or 0),
                warnings=_jsonable_warnings(canonical["warnings_json"]),
                failure_reason=canonical["failure_reason"],
                needs_visual_pass=bool(canonical["needs_visual_pass"]),
                needs_manual_projection=bool(canonical["needs_manual_projection"]),
                image_only_pdf=bool(canonical["image_only_pdf"]),
                duplicate_state=duplicate_state,
                canonical_archive_id=canonical_id,
            )
        raw_path = Path(str(row["artifact_path"] or row["path"])).resolve()
        if not raw_path.exists() or not raw_path.is_file():
            raise FileNotFoundError(str(raw_path))
        source_attachment_rows = connection.execute(
            """
            SELECT figure_id, attachment_path, mime_type, structural_location
            FROM artifact_figures
            WHERE archive_id = ? AND storage_policy = 'source_attachment'
            ORDER BY source_index, figure_id
            """,
            (archive_id,),
        ).fetchall()
    finally:
        connection.close()

    now = datetime.now()
    raw_bytes = raw_path.read_bytes()
    raw_hash = sha256(raw_bytes).hexdigest()
    connector = connector_for_path(raw_path)
    result = connector.extract(raw_path, config=connector_config or ConnectorConfig())
    metadata = dict(result.metadata)
    source_format = str(metadata.get("source_format") or getattr(connector, "source_format", "unknown"))
    warnings = list(result.warnings)
    figure_rows = [_jsonable_figure(figure) for figure in result.figures]
    figure_rows.extend(_figure_rows_from_source_attachments(source_attachment_rows, start_index=len(figure_rows) + 1, warnings=warnings))
    ingest_status = result.status
    if ingest_status not in {"accepted", "accepted_with_warnings", "failed_extraction", "manual_fix_needed"}:
        ingest_status = "failed_extraction"
        warnings.append(f"invalid_connector_status:{result.status}")
    failure_reason = result.failure_reason
    needs_manual_projection = bool(metadata.get("needs_manual_projection")) or ingest_status == "failed_extraction"
    needs_visual_pass = bool(metadata.get("needs_visual_pass")) or any(bool(item.get("needs_visual_pass")) for item in figure_rows)
    image_only_pdf = bool(metadata.get("image_only_pdf"))
    figure_count = max(int(metadata.get("figure_count") or 0), len(figure_rows))
    table_count = int(metadata.get("table_count") or 0)
    page_count = int(metadata.get("page_count") or 0)
    slide_count = int(metadata.get("slide_count") or 0)
    artifact_dir = raw_path.parent
    manifest_path = artifact_dir / "manifest.json"
    manifest = {
        "archive_id": archive_id,
        "artifact_path": str(raw_path.resolve()),
        "source_format": source_format,
        "original_filename": str(row["original_filename"] or raw_path.name),
        "raw_sha256": raw_hash,
        "byte_size": len(raw_bytes),
        "connector_name": str(metadata.get("connector_name") or getattr(connector, "name", "unknown_connector")),
        "connector_version": str(metadata.get("connector_version") or ""),
        "ingest_status": ingest_status,
        "page_count": page_count,
        "slide_count": slide_count,
        "figure_count": figure_count,
        "table_count": table_count,
        "warnings": warnings,
        "failure_reason": failure_reason,
        "needs_visual_pass": needs_visual_pass,
        "needs_manual_projection": needs_manual_projection,
        "image_only_pdf": image_only_pdf,
        "figures": figure_rows,
        "reprocessed_at": now.isoformat(timespec="seconds"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    try:
        with connection:
            connection.execute(
                """
                UPDATE archive
                SET type = ?,
                    path = ?,
                    artifact_path = ?,
                    source_format = ?,
                    original_filename = ?,
                    raw_sha256 = ?,
                    byte_size = ?,
                    connector_name = ?,
                    connector_version = ?,
                    ingest_status = ?,
                    page_count = ?,
                    slide_count = ?,
                    figure_count = ?,
                    table_count = ?,
                    warnings_json = ?,
                    failure_reason = ?,
                    needs_visual_pass = ?,
                    needs_manual_projection = ?,
                    image_only_pdf = ?
                WHERE id = ?
                """,
                (
                    _artifact_archive_type(source_format),
                    str(raw_path.resolve()),
                    str(raw_path.resolve()),
                    source_format,
                    manifest["original_filename"],
                    raw_hash,
                    len(raw_bytes),
                    manifest["connector_name"],
                    manifest["connector_version"],
                    ingest_status,
                    page_count,
                    slide_count,
                    figure_count,
                    table_count,
                    json.dumps(warnings),
                    failure_reason,
                    1 if needs_visual_pass else 0,
                    1 if needs_manual_projection else 0,
                    1 if image_only_pdf else 0,
                    archive_id,
                ),
            )
            _upsert_archive_sync_state(connection, raw_path, archive_id, raw_hash)
            connection.execute("DELETE FROM artifact_figures WHERE archive_id = ?", (archive_id,))
            _insert_artifact_figure_rows(connection, archive_id, figure_rows)
            identity = classify_post_parse_identity(
                connection,
                archive_id=archive_id,
                source_format=source_format,
                usable_raw_text=result.usable_raw_text,
            )
            update_archive_identity(connection, archive_id=archive_id, result=identity)
            write_identity_buckets(
                connection,
                archive_id=archive_id,
                usable_raw_text=result.usable_raw_text,
                duplicate_state=identity.duplicate_state,
            )
    finally:
        connection.close()

    manifest["duplicate_state"] = identity.duplicate_state
    manifest["canonical_archive_id"] = identity.canonical_archive_id
    manifest["identity_fingerprint_version"] = identity.identity_fingerprint_version
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    return IngestArtifactResult(
        archive_id=archive_id,
        raw_path=str(raw_path.resolve()),
        manifest_path=str(manifest_path.resolve()),
        ingest_status=ingest_status,
        source_format=source_format,
        figure_count=figure_count,
        warnings=warnings,
        failure_reason=failure_reason,
        needs_visual_pass=needs_visual_pass,
        needs_manual_projection=needs_manual_projection,
        image_only_pdf=image_only_pdf,
        duplicate_state=identity.duplicate_state,
        canonical_archive_id=identity.canonical_archive_id,
    )


def reprocess_failed_artifacts(config: AppConfig) -> list[IngestArtifactResult]:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    try:
        rows = connection.execute(
            """
            SELECT id
            FROM archive
            WHERE id LIKE 'artifact/%' AND ingest_status = 'failed_extraction'
            ORDER BY ingested_at, id
            """
        ).fetchall()
    finally:
        connection.close()
    return [reprocess_artifact(config, archive_id=str(row["id"])) for row in rows]



def _load_episode_event_payload(event: dict[str, Any], episode_dir: Path) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return {}
    if payload.get("externalized_payload") and payload.get("artifact"):
        artifact_path = episode_dir / str(payload["artifact"])
        try:
            loaded = json.loads(artifact_path.read_text())
            return loaded if isinstance(loaded, dict) else payload
        except (OSError, json.JSONDecodeError):
            return payload
    return payload


def _project_episode_markdown(
    *,
    conversation_id: str,
    episode_id: str,
    bridge_id: str,
    task_id: str | None,
    events: list[dict[str, Any]],
    episode_dir: Path,
) -> str:
    lines = [
        f"agent: {bridge_id}",
        f"session_date: {datetime.now().strftime('%Y-%m-%d')}",
        "task_kind: general",
        f"task_id: {task_id or ''}",
        f"conversation_id: {conversation_id}",
        f"episode_id: {episode_id}",
        "",
        "## Raw Episode Projection",
        "",
    ]
    for event in events:
        event_type = str(event.get("type") or "event")
        seq = event.get("seq")
        ts = event.get("ts")
        payload = _load_episode_event_payload(event, episode_dir)
        prefix = f"### {event_type}"
        if seq is not None:
            prefix += f" seq={seq}"
        if ts:
            prefix += f" ts={ts}"
        lines.extend([prefix, ""])
        if event_type in {"user_message", "assistant_message"}:
            content = str(payload.get("content") or "").strip()
            if content:
                lines.extend([content, ""])
                continue
        if event_type == "tool_call":
            lines.extend(
                [
                    f"tool_name: {payload.get('tool_name', '')}",
                    f"call_id: {payload.get('call_id', '')}",
                    "",
                    "```json",
                    json.dumps(payload.get("input") or {}, indent=2, sort_keys=True),
                    "```",
                    "",
                ]
            )
            continue
        if event_type == "tool_result":
            output = str(payload.get("output") or "").strip()
            lines.extend(
                [
                    f"tool_name: {payload.get('tool_name', '')}",
                    f"call_id: {payload.get('call_id', '')}",
                    f"is_error: {bool(payload.get('is_error'))}",
                    "",
                ]
            )
            if output:
                lines.extend(["```text", output, "```", ""])
            continue
        if event_type == "failure":
            message = str(payload.get("message") or "").strip()
            if message:
                lines.extend([message, ""])
            lines.extend(["```json", json.dumps(payload, indent=2, sort_keys=True), "```", ""])
            continue
        lines.extend(["```json", json.dumps(payload, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines).rstrip() + "\n"



def ingest_session(
    config: AppConfig,
    *,
    bridge_id: str | None = None,
    session_id: str | None = None,
    content: str,
    tags: list[str] | None = None,
    source_refs: list[str] | None = None,
    agent: str | None = None,
    slug: str | None = None,
) -> IngestSessionResult:
    resolved_bridge_id = (bridge_id or agent or "local").strip()
    resolved_session_id = (session_id or slug or datetime.now().strftime("%Y-%m-%d-%H%M%S")).strip()
    archive_id, raw_path, chunks, connection = _archive_session(
        config,
        resolved_bridge_id=resolved_bridge_id,
        resolved_session_id=resolved_session_id,
        content=content,
        tags=tags,
        skip_vectors=True,
    )
    connection.close()

    return IngestSessionResult(
        archive_id=archive_id,
        raw_path=str(raw_path.resolve()),
        chunk_count=len(chunks),
        promoted_notes=[],
        review_items=[],
    )

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import re
import sqlite3
from typing import Any

import yaml

from .config import AppConfig
from .db import connect_db, run_migrations
from .models import IngestSessionResult
from .review import create_review_item, slugify
from .semantics import encode_text, upsert_archive_chunk_vector
from .summaries import summarize_text
from .sync import sync_vault


MEMORY_BLOCK_PATTERN = re.compile(r"```(?:memory|mnemonic)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
INLINE_ITEM_PATTERN = re.compile(
    r"^\s*(Decision|Procedure|Incident|Concept|Project|Repo|Person|Source|Preference|Convention):\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
WORD_PATTERN = re.compile(r"\S+")

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
    metadata["title"] = item["title"]
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


def ingest_session(
    config: AppConfig,
    *,
    agent: str,
    slug: str,
    content: str,
    tags: list[str] | None = None,
    source_refs: list[str] | None = None,
) -> IngestSessionResult:
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d-%H%M%S")
    archive_name = f"{timestamp}-{slugify(agent)}-{slugify(slug)}"
    archive_id = f"session/{archive_name}"
    raw_path = config.paths.vault_root / "raw" / "sessions" / f"{archive_name}.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(content.rstrip() + "\n")

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")

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
                agent,
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
            if config.embeddings.enabled:
                vector = encode_text(summary or chunk, dimensions=config.embeddings.dimensions)
                upsert_archive_chunk_vector(
                    connection,
                    chunk_id=chunk_id,
                    model=config.embeddings.model,
                    dimensions=config.embeddings.dimensions,
                    vector=vector,
                    source_hash=content_hash,
                )

    raw_items = _read_memory_items(content, config.ingest.explicit_markers_only)
    promoted_notes: list[str] = []
    review_items: list[str] = []
    changed_paths: list[str] = []
    extra_source_refs = list(source_refs or [])

    for raw_item in raw_items:
        normalized = _normalize_item(raw_item, archive_id=archive_id, now=now)
        if normalized is None:
            continue
        normalized["source_refs"] = _merge_unique(normalized.get("source_refs", []), extra_source_refs)
        item_type = normalized["type"]
        confidence = str(normalized.get("confidence", "medium"))

        if item_type in {"preference", "convention"}:
            if confidence != "high":
                review_path = create_review_item(
                    config,
                    review_type="promotion_review",
                    suggested_action=f"review {item_type}",
                    reason=normalized["title"],
                    confidence=confidence,
                    agent=agent,
                    source_refs=list(normalized["source_refs"]),
                    context=normalized["summary"],
                    slug_seed=f"{item_type}-{normalized['title']}",
                )
                review_items.append(f"review/{review_path.stem}")
                continue

            if item_type == "preference":
                changed = _append_system_memory(
                    config.paths.vault_root / "system" / "USER.md",
                    "Preferences",
                    normalized["title"],
                )
                if changed:
                    changed_paths.append(str((config.paths.vault_root / "system" / "USER.md").resolve()))
                continue

            changed = _append_system_memory(
                config.paths.vault_root / "system" / "MEMORY.md",
                "Agent Behavior",
                normalized["title"],
            )
            if changed:
                changed_paths.append(str((config.paths.vault_root / "system" / "MEMORY.md").resolve()))
            continue

        if confidence != "high":
            review_path = create_review_item(
                config,
                review_type="promotion_review",
                suggested_action=f"promote {normalized['id']}",
                reason=normalized["title"],
                confidence=confidence,
                agent=agent,
                source_refs=list(normalized["source_refs"]),
                context=normalized.get("summary", ""),
                slug_seed=normalized["id"].replace("/", "-"),
            )
            review_items.append(f"review/{review_path.stem}")
            continue

        target_path = _path_for_item(config, normalized)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        metadata = dict(normalized)
        if normalized["type"] == "procedure":
            metadata.setdefault("status", "active" if normalized.get("reviewed_by") and normalized.get("reviewed_at") else "draft")
            if metadata["status"] == "active" and not (metadata.get("reviewed_by") and metadata.get("reviewed_at")):
                metadata["status"] = "draft"
        elif normalized["type"] == "decision":
            metadata.setdefault("status", "active")
            metadata.setdefault("valid_from", normalized["created_at"])
        elif normalized["type"] == "incident":
            metadata.setdefault("status", "open")
        elif normalized["type"] in {"project", "repo"}:
            metadata.setdefault("status", "active")

        body = _render_note_body(normalized["type"], normalized)
        if target_path.exists():
            from .parser import split_frontmatter

            parsed = split_frontmatter(target_path.read_text())
            metadata = _merge_note_metadata(parsed.metadata, metadata, now_date=now.strftime("%Y-%m-%d"))
            existing_body = parsed.body.strip()
            if len(existing_body) > len(body):
                body = existing_body

        target_path.write_text(_render_note(metadata, body))
        changed_paths.append(str(target_path.resolve()))
        promoted_notes.append(normalized["id"])

    sync_vault(config, mode="incremental", selected_paths=changed_paths)

    log_path = config.paths.vault_root / "wiki" / "log.md"
    log_lines = [log_path.read_text().rstrip()] if log_path.exists() else ["# Change Log", "", "# Format", "- `YYYY-MM-DD HH:MM | agent | action | note_id | reason`"]
    stamp = now.strftime("%Y-%m-%d %H:%M")
    for note_id in promoted_notes:
        log_lines.append(f"{stamp} | {agent} | ingested | {note_id} | promoted from {archive_id}")
    for review_id in review_items:
        log_lines.append(f"{stamp} | {agent} | review | {review_id} | needs promotion review from {archive_id}")
    log_path.write_text("\n".join(log_lines).rstrip() + "\n")
    connection.close()

    return IngestSessionResult(
        archive_id=archive_id,
        raw_path=str(raw_path.resolve()),
        chunk_count=len(chunks),
        promoted_notes=promoted_notes,
        review_items=review_items,
    )

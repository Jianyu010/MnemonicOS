from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

import yaml

from .bridge import decide_upsert, find_existing_candidates, infer_chain_context, parse_summary
from .config import AppConfig
from .db import connect_db, run_migrations
from .models import CandidateMatch, IngestSessionResult
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


def _enqueue_promotion_candidate(
    connection: sqlite3.Connection,
    *,
    archive_id: str,
    bridge_id: str,
    session_id: str,
    task_id: str | None,
    task_kind: str | None,
    content: str,
    summary_payload: dict[str, Any],
    chain_extraction: str,
    chain_reason: str,
) -> None:
    connection.execute(
        """
        INSERT INTO promotion_queue(
            archive_id, bridge_id, session_id, task_id, task_kind, content, summary_payload,
            chain_extraction, chain_reason, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        ON CONFLICT(archive_id) DO UPDATE SET
            bridge_id = excluded.bridge_id,
            session_id = excluded.session_id,
            task_id = excluded.task_id,
            task_kind = excluded.task_kind,
            content = excluded.content,
            summary_payload = excluded.summary_payload,
            chain_extraction = excluded.chain_extraction,
            chain_reason = excluded.chain_reason,
            status = CASE
                WHEN promotion_queue.status = 'processed' THEN promotion_queue.status
                ELSE 'pending'
            END,
            last_error = NULL
        """,
        (
            archive_id,
            bridge_id,
            session_id,
            task_id,
            task_kind,
            content,
            json.dumps(summary_payload, default=str),
            chain_extraction,
            chain_reason,
        ),
    )


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
    session_summary = parse_summary(content)
    chain_context = infer_chain_context(
        session_summary,
        session_id=resolved_session_id,
        task_id=session_summary.metadata.get("task_id", "").strip() or None,
    )
    with connection:
        _enqueue_promotion_candidate(
            connection,
            archive_id=archive_id,
            bridge_id=resolved_bridge_id,
            session_id=resolved_session_id,
            task_id=session_summary.metadata.get("task_id", "").strip() or None,
            task_kind=session_summary.metadata.get("task_kind", "").strip() or None,
            content=content,
            summary_payload={
                "metadata": session_summary.metadata,
                "sections": session_summary.sections,
                "memory_items": session_summary.memory_items,
                "source_refs": list(source_refs or []),
            },
            chain_extraction=chain_context.get("chain_extraction", "no_chain"),
            chain_reason=chain_context.get("chain_reason", ""),
        )
    connection.close()

    return IngestSessionResult(
        archive_id=archive_id,
        raw_path=str(raw_path.resolve()),
        chunk_count=len(chunks),
        promoted_notes=[],
        review_items=[],
    )


def _promote_session_content(
    config: AppConfig,
    *,
    connection: sqlite3.Connection,
    archive_id: str,
    resolved_bridge_id: str,
    resolved_session_id: str,
    content: str,
    source_refs: list[str] | None,
) -> tuple[list[str], list[str]]:
    now = datetime.now()
    raw_items = _read_memory_items(content, config.ingest.explicit_markers_only)
    session_summary = parse_summary(content)
    chain_context = infer_chain_context(
        session_summary,
        session_id=resolved_session_id,
        task_id=session_summary.metadata.get("task_id", "").strip() or None,
    )
    promoted_notes: list[str] = []
    review_items: list[str] = []
    changed_paths: list[str] = []
    extra_source_refs = list(source_refs or [])
    chain_seen_types: set[str] = set()
    new_count = 0
    update_count = 0
    confirm_count = 0
    skip_count = 0

    for raw_item in raw_items:
        normalized = _normalize_item(raw_item, archive_id=archive_id, now=now)
        if normalized is None:
            continue
        if chain_context.get("chain_extraction") != "no_chain":
            for key in ("chain_id", "chain_confidence", "chain_question", "chain_summary", "problem_class"):
                value = chain_context.get(key, "").strip()
                if value and not str(normalized.get(key, "")).strip():
                    normalized[key] = value
            chain_id_value = str(normalized.get("chain_id", "")).strip()
            if chain_id_value:
                relations = normalized.get("relations")
                if not isinstance(relations, list):
                    relations = []
                if not any(
                    isinstance(entry, dict)
                    and str(entry.get("type", "")).strip() == "part_of_chain"
                    and str(entry.get("target", "")).strip() == chain_id_value
                    for entry in relations
                ):
                    relations = [*relations, {"type": "part_of_chain", "target": chain_id_value}]
                normalized["relations"] = relations
        normalized["source_refs"] = _merge_unique(normalized.get("source_refs", []), extra_source_refs)
        item_type = normalized["type"]
        confidence = str(normalized.get("confidence", "medium"))
        chain_id = str(normalized.get("chain_id", "")).strip() or None

        if chain_id and item_type in CHAIN_BOUNDED_TYPES and item_type in chain_seen_types:
            review_path = create_review_item(
                config,
                review_type="bounded_promotion_review",
                suggested_action=f"review extra {item_type} for {chain_id}",
                reason=f"additional {item_type} candidate within bounded chain promotion",
                confidence="medium",
                bridge_id=resolved_bridge_id,
                source_refs=list(normalized["source_refs"]),
                context=normalized.get("summary", "") or normalized.get("body", ""),
                slug_seed=f"bounded-{chain_id.replace('/', '-')}-{item_type}",
                extra_metadata={
                    "chain_id": chain_id,
                    "note_type": item_type,
                    "candidate_title": normalized["title"],
                },
            )
            review_items.append(f"review/{review_path.stem}")
            continue

        if item_type in {"preference", "convention"}:
            if confidence != "high":
                review_path = create_review_item(
                    config,
                    review_type="promotion_review",
                    suggested_action=f"review {item_type}",
                    reason=normalized["title"],
                    confidence=confidence,
                    bridge_id=resolved_bridge_id,
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
                bridge_id=resolved_bridge_id,
                source_refs=list(normalized["source_refs"]),
                context=normalized.get("summary", ""),
                slug_seed=normalized["id"].replace("/", "-"),
            )
            review_items.append(f"review/{review_path.stem}")
            continue

        item_candidates = find_existing_candidates(
            connection,
            config,
            note_type=normalized["type"],
            title=normalized["title"],
            summary=normalized.get("summary", "") or normalized.get("body", "") or normalized["title"],
        )
        same_chain_target = _same_chain_candidate(connection, note_type=normalized["type"], chain_id=chain_id)
        if chain_id:
            if same_chain_target:
                item_candidates = [
                    CandidateMatch(
                        note_id=same_chain_target,
                        title=normalized["title"],
                        note_type=normalized["type"],
                        score=1.01,
                        reason="same_chain",
                    )
                ]
            else:
                item_candidates = []
        upsert_decision = decide_upsert(
            connection,
            config,
            summary=session_summary,
            note_type=normalized["type"],
            title=normalized["title"],
            summary_text=normalized.get("summary", "") or normalized.get("body", "") or normalized["title"],
            existing_candidates=item_candidates,
        )
        if upsert_decision.decision == "skip_duplicate":
            skip_count += 1
            continue
        if upsert_decision.decision == "needs_review":
            review_path = create_review_item(
                config,
                review_type="merge_review",
                suggested_action=f"review duplicate candidate for {normalized['id']}",
                reason=upsert_decision.reason,
                confidence="medium",
                bridge_id=resolved_bridge_id,
                source_refs=list(normalized["source_refs"]),
                candidates=upsert_decision.candidates,
                context=normalized.get("summary", ""),
                slug_seed=f"duplicate-{normalized['id'].replace('/', '-')}",
                extra_metadata={"target_candidates": upsert_decision.targets},
            )
            review_items.append(f"review/{review_path.stem}")
            continue

        target_path = _path_for_item(config, normalized)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_preexisting = target_path.exists()
        metadata = dict(normalized)
        if upsert_decision.targets:
            metadata["linked_notes"] = _merge_unique(list(metadata.get("linked_notes") or []), list(upsert_decision.targets))
            if upsert_decision.decision == "update_existing":
                metadata["source_refs"] = _merge_unique(list(metadata.get("source_refs") or []), list(upsert_decision.targets))
        metadata = _apply_note_defaults(metadata, normalized["type"])

        if upsert_decision.decision in {"update_existing", "confirm_existing"} and upsert_decision.targets:
            existing_id = upsert_decision.targets[0]
            existing_slug = existing_id.split("/", 1)[1]
            if normalized["type"] == "procedure":
                procedure_root = config.paths.vault_root / "wiki" / "procedures"
                for directory in ("drafts", "active", "retired"):
                    candidate_path = procedure_root / directory / f"{existing_slug}.md"
                    if candidate_path.exists():
                        target_path = candidate_path
                        break
            else:
                target_path = _path_for_item(config, {"type": normalized["type"], "id": existing_id})
            metadata["id"] = existing_id
            if upsert_decision.decision == "confirm_existing" and target_path.exists():
                if _touch_existing_note(target_path, list(metadata.get("source_refs") or []), now.strftime("%Y-%m-%d")):
                    changed_paths.append(str(target_path.resolve()))
                    promoted_notes.append(existing_id)
                    confirm_count += 1
                    if chain_id and item_type in CHAIN_BOUNDED_TYPES:
                        chain_seen_types.add(item_type)
                continue

        body = _render_note_body(normalized["type"], normalized)
        if target_path.exists():
            from .parser import split_frontmatter

            parsed = split_frontmatter(target_path.read_text())
            metadata = _merge_note_metadata(parsed.metadata, metadata, now_date=now.strftime("%Y-%m-%d"))
            metadata = _apply_note_defaults(metadata, normalized["type"])
            existing_body = parsed.body.strip()
            if len(existing_body) > len(body):
                body = existing_body

        metadata = _project_obsidian_links(config, metadata)
        target_path.write_text(_render_note(metadata, body))
        changed_paths.append(str(target_path.resolve()))
        promoted_notes.append(normalized["id"])
        if upsert_decision.decision == "update_existing" or target_preexisting:
            update_count += 1
        else:
            new_count += 1
        if chain_id and item_type in CHAIN_BOUNDED_TYPES:
            chain_seen_types.add(item_type)

    sync_vault(config, mode="incremental", selected_paths=changed_paths)

    log_path = config.paths.vault_root / "wiki" / "log.md"
    log_lines = [log_path.read_text().rstrip()] if log_path.exists() else ["# Change Log", "", "# Format", "- `YYYY-MM-DD HH:MM | bridge_id | action | note_id | reason`"]
    stamp = now.strftime("%Y-%m-%d %H:%M")
    for note_id in promoted_notes:
        log_lines.append(f"{stamp} | {resolved_bridge_id} | ingested | {note_id} | promoted from {archive_id}")
    for review_id in review_items:
        log_lines.append(f"{stamp} | {resolved_bridge_id} | review | {review_id} | needs promotion review from {archive_id}")
    log_path.write_text("\n".join(log_lines).rstrip() + "\n")
    with connection:
        connection.execute(
            """
            INSERT INTO session_metrics(
                session_id, bridge_id, archive_id, task_id, task_kind, chain_extraction, chain_reason,
                promoted_count, review_count, new_count, update_count, confirm_count, skip_count, note_delta
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                bridge_id = excluded.bridge_id,
                archive_id = excluded.archive_id,
                task_id = excluded.task_id,
                task_kind = excluded.task_kind,
                chain_extraction = excluded.chain_extraction,
                chain_reason = excluded.chain_reason,
                promoted_count = excluded.promoted_count,
                review_count = excluded.review_count,
                new_count = excluded.new_count,
                update_count = excluded.update_count,
                confirm_count = excluded.confirm_count,
                skip_count = excluded.skip_count,
                note_delta = excluded.note_delta
            """,
            (
                resolved_session_id,
                resolved_bridge_id,
                archive_id,
                session_summary.metadata.get("task_id", "").strip() or None,
                session_summary.metadata.get("task_kind", "").strip() or None,
                chain_context.get("chain_extraction", "no_chain"),
                chain_context.get("chain_reason", ""),
                len(promoted_notes),
                len(review_items),
                new_count,
                update_count,
                confirm_count,
                skip_count,
                new_count,
            ),
        )
    return promoted_notes, review_items


def ingest_session_promote(
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
        skip_vectors=False,
    )
    promoted_notes, review_items = _promote_session_content(
        config,
        connection=connection,
        archive_id=archive_id,
        resolved_bridge_id=resolved_bridge_id,
        resolved_session_id=resolved_session_id,
        content=content,
        source_refs=source_refs,
    )
    connection.close()

    return IngestSessionResult(
        archive_id=archive_id,
        raw_path=str(raw_path.resolve()),
        chunk_count=len(chunks),
        promoted_notes=promoted_notes,
        review_items=review_items,
    )

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from ..ingest import LIFECYCLE_DEFAULTS, TYPE_TO_DIRECTORY, _render_note_body
from ..parser import split_frontmatter
from ..review import slugify
from .models import PromotionDecision

# Procedures live under wiki/procedures/drafts by default
_PROCEDURE_DEFAULT_SUBDIR = "drafts"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _note_dir(note_type: str) -> str:
    if note_type == "procedure":
        return "procedures"
    return TYPE_TO_DIRECTORY.get(note_type, note_type + "s")


def _note_path_relative(note_type: str, slug: str) -> str:
    if note_type == "procedure":
        return f"wiki/procedures/{_PROCEDURE_DEFAULT_SUBDIR}/{slug}.md"
    return f"wiki/{_note_dir(note_type)}/{slug}.md"


def render(
    candidate: dict[str, Any],
    *,
    decision: PromotionDecision,
    candidate_id: int,
    archive_id: str,
    existing_note_path: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Convert a candidate payload + decision into (metadata_dict, rendered_markdown).

    Returns (metadata, markdown). Does not write any files.
    """
    note_type = str(candidate.get("type") or "").strip()
    title = str(candidate.get("title") or "").strip()
    slug = slugify(title)
    note_id = f"{note_type}/{slug}"
    now = _now_iso()

    # Build provenance source refs
    source_refs: list[str] = list(candidate.get("source_refs") or [])
    source_refs.append(f"candidate:{candidate_id}")
    if archive_id:
        source_refs.append(f"archive:{archive_id}")
    source_refs = list(dict.fromkeys(source_refs))  # deduplicate preserving order

    if decision.decision in {"confirm", "update"} and existing_note_path is not None and existing_note_path.exists():
        return _render_existing(
            candidate,
            decision=decision,
            existing_note_path=existing_note_path,
            source_refs=source_refs,
            now=now,
        )

    return _render_new(candidate, note_id=note_id, note_type=note_type, source_refs=source_refs, now=now, decision=decision)


def _render_new(
    candidate: dict[str, Any],
    *,
    note_id: str,
    note_type: str,
    source_refs: list[str],
    now: str,
    decision: PromotionDecision,
) -> tuple[dict[str, Any], str]:
    title = str(candidate.get("title") or "").strip()
    confidence = str(candidate.get("confidence") or "medium").strip()
    summary = str(candidate.get("summary") or "").strip()

    metadata: dict[str, Any] = {
        "id": note_id,
        "type": note_type,
        "title": title,
        "summary": summary,
        "confidence": confidence,
        "source_refs": source_refs,
        "created_at": now,
        "updated_at": now,
        "lifecycle_state": LIFECYCLE_DEFAULTS.get(note_type, "draft"),
        "corroboration_count": 1,
    }

    # Propagate corroboration_count increment for confirm
    if decision.decision == "confirm":
        metadata["corroboration_count"] = 1

    # Chain ID from single trace
    traces = candidate.get("traces") or []
    if isinstance(traces, list) and len(traces) == 1:
        trace_id = str(traces[0].get("trace_id") or "").strip()
        if trace_id:
            metadata["chain_id"] = trace_id

    # Type-specific fields extracted from components
    components = candidate.get("components") or []
    if isinstance(components, list):
        steps = [
            str(c.get("text") or "").strip()
            for c in components
            if isinstance(c, dict) and c.get("component_type") == "procedure_step"
        ]
        failure_modes = [
            str(c.get("text") or "").strip()
            for c in components
            if isinstance(c, dict) and c.get("component_type") == "failure"
        ]
        if steps:
            metadata["steps"] = steps
        if failure_modes:
            metadata["failure_modes"] = failure_modes

    # Apply type-specific status defaults
    if note_type == "procedure":
        metadata.setdefault("status", "draft")
    elif note_type in {"decision", "project", "repo"}:
        metadata.setdefault("status", "active")
    elif note_type == "incident":
        metadata.setdefault("status", "open")

    # Build body using ingest's type-aware body renderer
    body = _build_body(note_type, candidate, components)

    # For review routing, prepend a header
    if decision.decision == "review":
        review_header = f"## Promotion Review\n\nReason: {decision.reason}\n\n"
        body = review_header + body
        metadata["lifecycle_state"] = "review"

    rendered = _render_note_md(metadata, body)
    return metadata, rendered


_CONFIDENCE_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_CONFIDENCE_LEVELS = ["low", "medium", "high"]


def _render_existing(
    candidate: dict[str, Any],
    *,
    decision: PromotionDecision,
    existing_note_path: Path,
    source_refs: list[str],
    now: str,
) -> tuple[dict[str, Any], str]:
    parsed = split_frontmatter(existing_note_path.read_text())
    metadata = dict(parsed.metadata)
    existing_body = parsed.body

    # Always update source_refs and timestamps
    existing_refs: list[str] = list(metadata.get("source_refs") or [])
    for ref in source_refs:
        if ref not in existing_refs:
            existing_refs.append(ref)
    metadata["source_refs"] = existing_refs
    metadata["updated_at"] = now
    metadata["last_observed_at"] = now

    # Increment corroboration count
    metadata["corroboration_count"] = int(metadata.get("corroboration_count") or 1) + 1

    # Determine if this is a conflict-driven decision (from time-weighted resolution)
    reason = str(decision.reason or "")
    is_conflict = reason.startswith("conflict_")

    current_lc = str(metadata.get("lifecycle_state") or "draft").strip()
    count = int(metadata["corroboration_count"])

    if not is_conflict:
        # Standard lifecycle advancement on corroboration threshold
        if current_lc == "draft" and count >= 2:
            metadata["lifecycle_state"] = "verified"
        elif current_lc == "verified" and count >= 4:
            metadata["lifecycle_state"] = "canonical"
    else:
        # Conflict-driven lifecycle mutations based on time-weighted resolution reason
        if "supersedes_stale" in reason:
            metadata["lifecycle_state"] = "stale"
        elif "supersedes_contested" in reason:
            if current_lc not in {"contested", "stale", "archived"}:
                metadata["lifecycle_state"] = "contested"
            # Decrement confidence by one level
            old_conf = str(metadata.get("confidence") or "medium").strip()
            old_rank = _CONFIDENCE_RANK.get(old_conf, 1)
            if old_rank > 0:
                metadata["confidence"] = _CONFIDENCE_LEVELS[old_rank - 1]
        elif "supersedes_archive" in reason:
            metadata["lifecycle_state"] = "archived"
        # conflict_corroborates: no lifecycle change, just timestamp refresh

        # Write corroborates/supersedes relation on the canonical note
        # pointing to the incoming archive source (enables reverse_corroborates via recall.py)
        archive_ref = next((r for r in source_refs if r.startswith("archive:")), None)
        if archive_ref:
            rel_type = "supersedes" if "supersedes" in reason else "corroborates"
            existing_relations: list[Any] = list(metadata.get("relations") or [])
            rel_entry: dict[str, str] = {"type": rel_type, "target": archive_ref}
            if rel_entry not in existing_relations:
                existing_relations.append(rel_entry)
            metadata["relations"] = existing_relations

    if decision.decision == "confirm":
        # Confirm: update confidence if incoming is stronger; do not rewrite body
        incoming_confidence = str(candidate.get("confidence") or "").strip()
        existing_conf = str(metadata.get("confidence") or "medium").strip()
        if _CONFIDENCE_RANK.get(incoming_confidence, 0) > _CONFIDENCE_RANK.get(existing_conf, 1):
            metadata["confidence"] = incoming_confidence
        body = existing_body

    elif decision.decision == "update":
        # Update: merge body — append new content not already present
        candidate_body = str(candidate.get("body") or "").strip()
        if candidate_body and candidate_body not in existing_body:
            body = existing_body.rstrip() + "\n\n" + candidate_body
        else:
            body = existing_body
        # Update confidence if stronger
        incoming_confidence = str(candidate.get("confidence") or "").strip()
        existing_conf = str(metadata.get("confidence") or "medium").strip()
        if _CONFIDENCE_RANK.get(incoming_confidence, 0) > _CONFIDENCE_RANK.get(existing_conf, 1):
            metadata["confidence"] = incoming_confidence
    else:
        body = existing_body

    rendered = _render_note_md(metadata, body)
    return metadata, rendered


def _build_body(note_type: str, candidate: dict[str, Any], components: list[Any]) -> str:
    """Build body string by delegating to ingest's type-aware renderer."""
    # Build an item dict that _render_note_body understands
    item: dict[str, Any] = {
        "title": str(candidate.get("title") or "").strip(),
        "summary": str(candidate.get("summary") or "").strip(),
        "body": str(candidate.get("body") or "").strip(),
    }
    if isinstance(components, list):
        steps = [
            str(c.get("text") or "")
            for c in components
            if isinstance(c, dict) and c.get("component_type") == "procedure_step"
        ]
        failure_modes = [
            str(c.get("text") or "")
            for c in components
            if isinstance(c, dict) and c.get("component_type") == "failure"
        ]
        if steps:
            item["steps"] = steps
        if failure_modes:
            item["failure_modes"] = failure_modes

    base = _render_note_body(note_type, item)

    # Append component sub-sections for non-trivial component types
    _SKIP_COMPONENT_TYPES = {"procedure_step", "failure"}
    section_lines: list[str] = []
    component_type_seen: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            continue
        ctype = str(component.get("component_type") or "").strip()
        text = str(component.get("text") or "").strip()
        if not text or ctype in _SKIP_COMPONENT_TYPES:
            continue
        if ctype not in component_type_seen:
            section_lines.append(f"\n### {ctype.replace('_', ' ').title()}")
            component_type_seen.add(ctype)
        section_lines.append(f"\n{text}")

    if section_lines:
        return base + "\n" + "\n".join(section_lines)
    return base


def _render_note_md(metadata: dict[str, Any], body: str) -> str:
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"

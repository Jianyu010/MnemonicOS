from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

import yaml

from .config import AppConfig
from .parser import split_frontmatter


SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    lowered = text.casefold().strip()
    slug = SLUG_PATTERN.sub("-", lowered).strip("-")
    return slug or "item"


def _review_path(config: AppConfig, slug: str) -> Path:
    return config.paths.vault_root / "wiki" / "review" / f"{slug}.md"


def create_review_item(
    config: AppConfig,
    *,
    review_type: str,
    suggested_action: str,
    reason: str,
    confidence: str,
    agent: str,
    source_refs: list[str],
    context: str = "",
    candidates: list[str] | None = None,
    slug_seed: str | None = None,
    extra_metadata: dict[str, object] | None = None,
) -> Path:
    timestamp = datetime.now().replace(microsecond=0).isoformat()
    slug = slugify(slug_seed or f"{review_type}-{reason}")
    path = _review_path(config, slug)
    path.parent.mkdir(parents=True, exist_ok=True)

    created_at = timestamp
    check_count = 0
    resolved_at = None
    resolved_by = None
    resolution = ""

    if path.exists():
        parsed = split_frontmatter(path.read_text())
        metadata = parsed.metadata
        created_at = str(metadata.get("created_at", created_at))
        check_count = int(metadata.get("check_count", 0)) + 1
        resolved_at = metadata.get("resolved_at")
        resolved_by = metadata.get("resolved_by")
        resolution = str(metadata.get("resolution", "") or "")

    review_id = f"review/{slug}"
    metadata = {
        "id": review_id,
        "type": review_type,
        "status": "open" if not resolved_at else "resolved",
        "created_at": created_at,
        "resolved_at": resolved_at,
        "resolved_by": resolved_by,
        "check_count": check_count,
        "candidates": candidates or [],
        "reason": reason,
        "suggested_action": suggested_action,
        "confidence": confidence,
        "source_refs": source_refs,
        "agent": agent,
        "resolution": resolution,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).strip()
    body = "## Context\n\n"
    body += context.strip() if context.strip() else "No additional context recorded."
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n")
    return path

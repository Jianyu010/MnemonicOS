from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..db import connect_db, run_migrations
from ..ingest import LIFECYCLE_DEFAULTS, TYPE_TO_DIRECTORY
from ..parser import NoteParseError, split_frontmatter
from ..review import slugify
from ..sync import sync_vault
from .writer import _append_log

if TYPE_CHECKING:
    from ..config import AppConfig

_REVIEW_HEADER_RE = re.compile(
    r"^## Promotion Review\s*\nReason:[^\n]*\n",
    re.MULTILINE,
)


@dataclass(slots=True)
class ReviewItem:
    filename: str
    path: Path
    note_id: str
    title: str
    note_type: str
    reason: str
    created_at: str


def _parse_review_file(path: Path) -> ReviewItem:
    text = path.read_text(encoding="utf-8")
    parsed = split_frontmatter(text)
    meta = parsed.metadata
    body = parsed.body
    if meta.get("lifecycle_state") != "review":
        raise NoteParseError(f"not a promotion review (lifecycle_state != review): {path.name}")
    if not re.search(r"^## Promotion Review", body, re.MULTILINE):
        raise NoteParseError(f"missing ## Promotion Review header: {path.name}")
    m = re.search(r"^Reason:\s*(.+)", body, re.MULTILINE)
    reason = m.group(1).strip() if m else "unknown"
    return ReviewItem(
        filename=path.name,
        path=path,
        note_id=str(meta.get("id") or ""),
        title=str(meta.get("title") or ""),
        note_type=str(meta.get("type") or "source"),
        reason=reason,
        created_at=str(meta.get("created_at") or ""),
    )


def list_review_items(config: AppConfig) -> list[ReviewItem]:
    review_dir = config.paths.vault_root / "wiki" / "review"
    if not review_dir.exists():
        return []
    items: list[ReviewItem] = []
    for path in sorted(review_dir.glob("*.md")):
        try:
            items.append(_parse_review_file(path))
        except (NoteParseError, OSError, KeyError):
            pass
    items.sort(key=lambda it: it.created_at)
    return items


def get_review_item(config: AppConfig, name: str) -> ReviewItem:
    review_dir = config.paths.vault_root / "wiki" / "review"
    # Exact match with or without .md
    candidate = name if name.endswith(".md") else name + ".md"
    exact = review_dir / candidate
    if exact.exists():
        return _parse_review_file(exact)
    # Prefix match
    for path in sorted(review_dir.glob("*.md")):
        if path.name.startswith(name):
            return _parse_review_file(path)
    raise FileNotFoundError(f"no review item matching {name!r} in {review_dir}")


def _strip_review_header(body: str) -> str:
    """Remove the '## Promotion Review\\n\\nReason: ...\\n\\n' block from body."""
    # Remove the header line
    cleaned = re.sub(r"^## Promotion Review\s*\n", "", body, flags=re.MULTILINE)
    # Remove the Reason line
    cleaned = re.sub(r"^Reason:[^\n]*\n?", "", cleaned, flags=re.MULTILINE)
    return cleaned.lstrip("\n")


def _final_path(vault_root: Path, note_type: str, title: str) -> Path:
    slug = slugify(title)
    if note_type == "procedure":
        return vault_root / "wiki" / "procedures" / "drafts" / f"{slug}.md"
    directory = TYPE_TO_DIRECTORY.get(note_type, "sources")
    return vault_root / "wiki" / directory / f"{slug}.md"


def _render_note(metadata: dict[str, Any], body: str) -> str:
    front = yaml.dump(metadata, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{front}---\n\n{body}"


def approve_review_item(
    config: AppConfig,
    name: str,
    *,
    lifecycle_state: str | None = None,
    dry_run: bool = False,
) -> Path:
    item = get_review_item(config, name)
    text = item.path.read_text(encoding="utf-8")
    parsed = split_frontmatter(text)
    metadata: dict[str, Any] = dict(parsed.metadata)
    body = _strip_review_header(parsed.body)

    note_type = str(metadata.get("type") or "source")
    effective_lifecycle = lifecycle_state or LIFECYCLE_DEFAULTS.get(note_type, "draft")
    metadata["lifecycle_state"] = effective_lifecycle
    metadata["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    vault_root = config.paths.vault_root
    final = _final_path(vault_root, note_type, str(metadata.get("title") or ""))

    if final.exists():
        raise FileExistsError(
            f"target already exists: {final}\n"
            "resolve conflict manually or use mnemonic review show to inspect both notes"
        )

    if dry_run:
        print(f"dry_run: would move {item.path.name}")
        print(f"  → {final}")
        print(f"  lifecycle_state={effective_lifecycle}")
        return final

    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_suffix(".tmp")
    tmp.write_text(_render_note(metadata, body), encoding="utf-8")

    note_id = str(metadata.get("id") or "")
    conn = connect_db(config.paths.db_path)
    run_migrations(conn, config.paths.workspace_root / "migrations")
    try:
        with conn:
            conn.execute(
                """
                UPDATE promotion_decisions SET promotion_state = 'promoted'
                WHERE target_note_id = ? AND promotion_state = 'review_routed'
                """,
                (note_id,),
            )
        os.rename(tmp, final)
        item.path.unlink()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    sync_vault(config, mode="incremental", selected_paths=[str(final)])
    _append_log(vault_root, "cli:review-approve", "approved", note_id, item.reason)

    return final


def reject_review_item(
    config: AppConfig,
    name: str,
    *,
    dry_run: bool = False,
) -> None:
    item = get_review_item(config, name)

    if dry_run:
        print(f"dry_run: would reject {item.path.name}")
        return

    note_id = item.note_id
    conn = connect_db(config.paths.db_path)
    run_migrations(conn, config.paths.workspace_root / "migrations")
    try:
        with conn:
            conn.execute(
                """
                UPDATE promotion_decisions SET promotion_state = 'rejected'
                WHERE target_note_id = ? AND promotion_state = 'review_routed'
                """,
                (note_id,),
            )
    finally:
        conn.close()

    item.path.unlink()
    _append_log(config.paths.vault_root, "cli:review-reject", "rejected", note_id, item.reason)

#!/usr/bin/env python3
"""Consolidation job: re-enrich wiki notes promoted from old raw sessions.

For each raw session file:
  1. Extract memory items (same logic as promotion pipeline).
  2. Synthesize summary (option A) and extract error aliases (option B).
  3. Find the wiki note with matching source_ref.
  4. If the wiki note summary is empty or title-copy, update it.
  5. Merge new aliases into the wiki note's aliases list.
  6. Write the updated file back.

Does NOT re-promote or create new notes. Only enriches existing wiki notes.
Run after implementing _synthesize_summary / _extract_error_aliases in ingest.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

import yaml

from src.second_brain.ingest import (
    _extract_error_aliases,
    _merge_unique,
    _read_memory_items,
    _synthesize_summary,
)


def _load_frontmatter(path: Path) -> tuple[dict, str]:
    """Return (metadata_dict, body_str) from a markdown file with YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    metadata = yaml.safe_load(parts[1]) or {}
    body = parts[2]
    return metadata, body


def _render_note(metadata: dict, body: str) -> str:
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{frontmatter}\n---\n{body}"


def _find_wiki_notes_by_source_ref(vault_root: Path, session_id: str) -> list[Path]:
    """Find all wiki note files whose source_refs include session_id."""
    matches = []
    for md in vault_root.rglob("wiki/**/*.md"):
        try:
            meta, _ = _load_frontmatter(md)
        except Exception:
            continue
        refs = meta.get("source_refs") or []
        if any(session_id in str(r) for r in refs):
            matches.append(md)
    return matches


def _note_matches_item(metadata: dict, item: dict) -> bool:
    """Return True when an existing note is the promoted form of this raw item."""
    item_type = str(item.get("type", "")).strip().casefold()
    note_type = str(metadata.get("type", "")).strip().casefold()
    if item_type and note_type and item_type != note_type:
        return False

    item_title = str(item.get("title", "")).strip().casefold()
    note_title = str(metadata.get("title", "")).strip().casefold()
    if item_title and note_title == item_title:
        return True

    item_id = str(item.get("id", "")).strip().casefold()
    note_id = str(metadata.get("id", "")).strip().casefold()
    return bool(item_id and note_id == item_id)


def consolidate(vault_root: Path, raw_dir: Path, *, dry_run: bool = False) -> None:
    session_files = sorted(raw_dir.glob("*.md"))
    print(f"Processing {len(session_files)} raw sessions...")

    updated = 0
    skipped = 0
    no_match = 0

    for session_path in session_files:
        content = session_path.read_text(encoding="utf-8")
        items = _read_memory_items(content, explicit_only=False)
        if not items:
            continue

        # Derive session_id from filename (stem without extension)
        session_id = session_path.stem

        wiki_notes = _find_wiki_notes_by_source_ref(vault_root, session_id)
        if not wiki_notes:
            no_match += 1
            continue

        for item in items:
            synthesized = _synthesize_summary(item, content)
            error_aliases = _extract_error_aliases(item, content)

            for note_path in wiki_notes:
                try:
                    meta, body = _load_frontmatter(note_path)
                except Exception as exc:
                    print(f"  SKIP {note_path.name}: {exc}")
                    continue
                if not _note_matches_item(meta, item):
                    skipped += 1
                    continue

                changed = False
                summary_changed = False
                aliases_changed = False
                title = str(meta.get("title", "")).strip()

                # Option A: update summary if empty or title-copy
                existing_summary = str(meta.get("summary", "")).strip()
                if synthesized and (
                    not existing_summary
                    or existing_summary.casefold() == title.casefold()
                ):
                    meta["summary"] = synthesized
                    changed = True
                    summary_changed = True

                # Option B: merge error aliases
                if error_aliases:
                    current_aliases = list(meta.get("aliases") or [])
                    merged = _merge_unique(current_aliases, error_aliases)
                    if merged != current_aliases:
                        meta["aliases"] = merged
                        changed = True
                        aliases_changed = True

                if not changed:
                    skipped += 1
                    continue

                if dry_run:
                    print(f"  [DRY] Would update {note_path.name}")
                    if summary_changed:
                        print(f"        summary → {synthesized[:120]}")
                    if aliases_changed:
                        print(f"        +aliases → {error_aliases}")
                else:
                    note_path.write_text(_render_note(meta, body), encoding="utf-8")
                    print(f"  UPDATED {note_path.name}")
                    if summary_changed:
                        print(f"    summary → {synthesized[:120]}")
                    if aliases_changed:
                        print(f"    +aliases → {error_aliases}")
                updated += 1

    print(f"\nDone. updated={updated} skipped={skipped} no_wiki_match={no_match}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault", default="brain", help="Path to vault root (default: brain)"
    )
    parser.add_argument(
        "--raw-dir",
        default="brain/raw/sessions",
        help="Path to raw sessions directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing files",
    )
    args = parser.parse_args()

    vault_root = Path(args.vault).resolve()
    raw_dir = Path(args.raw_dir).resolve()

    if not vault_root.exists():
        print(f"ERROR: vault not found: {vault_root}", file=sys.stderr)
        sys.exit(1)
    if not raw_dir.exists():
        print(f"ERROR: raw dir not found: {raw_dir}", file=sys.stderr)
        sys.exit(1)

    consolidate(vault_root, raw_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

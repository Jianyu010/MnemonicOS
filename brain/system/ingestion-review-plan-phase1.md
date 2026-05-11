# Review Command — Phase 1: Core Logic

## Goal

Implement the data layer for review operations: scanning the review dir, resolving
items by name, approving (move + sync), and rejecting (delete + DB update).

## Context

Promotion worker writes review-routed candidates to `brain/wiki/review/{slug}-{timestamp}.md`
with `lifecycle_state: review` and `## Promotion Review\n\nReason: <reason>` header.
No code existed to list or act on these files. Phase 1 creates the action functions
that Phase 2 (CLI) and Phase 3 (TUI) both call.

## Scope

**New file:** `src/second_brain/promotion/review_ops.py`

### `ReviewItem` dataclass

```python
@dataclass(slots=True)
class ReviewItem:
    filename: str       # e.g. "hook-adapter-test-plan-2026-05-08-08-57-41.md"
    path: Path
    note_id: str        # frontmatter "id" field
    title: str
    note_type: str      # frontmatter "type"
    reason: str         # extracted from body after "## Promotion Review"
    created_at: str
```

### Functions

- `list_review_items(config) -> list[ReviewItem]`
  Sort by `created_at` ascending. Skip files where `lifecycle_state != "review"` or
  body lacks `## Promotion Review\nReason:` header. Skip unparseable files.

- `get_review_item(config, name) -> ReviewItem`
  Exact filename, then `+.md`, then prefix match. Raises `FileNotFoundError`.

- `approve_review_item(config, name, *, lifecycle_state=None, dry_run=False) -> Path`
  Strip header → lifecycle → write tmp → update `promotion_decisions` (DB txn) →
  `os.rename(tmp, final)` → delete review file → `sync_vault` → append `wiki/log.md`.
  On DB failure: delete tmp, re-raise (review file intact).
  Raises `FileExistsError` if final path occupied.

- `reject_review_item(config, name, *, dry_run=False) -> None`
  Update `promotion_decisions` → delete file → append `wiki/log.md`.

Reuses: `split_frontmatter`, `slugify`, `TYPE_TO_DIRECTORY`, `LIFECYCLE_DEFAULTS`,
`sync_vault`, `_append_log`, `connect_db`.

## Milestones

1. `ReviewItem` + `_parse_review_file` → parses real review files
2. `list_review_items` + `get_review_item` → scan + resolve
3. `approve_review_item` with dry_run → move + sync
4. `reject_review_item` with dry_run → delete + DB

## Verification

`python -m pytest tests/test_review_ops.py -v` → 12 tests passing.

## Out of Scope

CLI display, interactive UI, HTTP surface.

## Status

**Complete** (implemented 2026-05-08). 12/12 tests pass.

**Loopholes found 2026-05-09** — see `ingestion-review-plan.md §Known Loopholes`.
Fixes tracked in Phase 4.

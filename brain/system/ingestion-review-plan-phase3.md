# Review Command — Phase 3: Interactive TUI

## Goal

Make `mnemonic review` (no subcommand) launch a full-screen interactive review
session. Show each pending item with rich formatting that highlights the problem,
links conflicting wiki entries, and lets the user approve/reject/skip with single
keystrokes or arrow keys.

## Context

Phase 2 CLI is pipe-friendly but requires knowing filenames. Human review of 8-20
items per extraction batch is tedious with per-command invocations. The interactive
mode shows items sequentially, surfaces the exact conflict, and requires only one
keystroke per decision.

`rich` (14.x) and `prompt_toolkit` (3.x) are already installed.

## Scope

**New file:** `src/second_brain/promotion/review_tui.py`

### Layout (single item view)

```
╭─ Review Queue ─────────────────────── 1 / 8 ─╮
│  ⚠  WIKI CONFLICT                             │
│                                               │
│  Title:    Hook Adapter Testing Task Def      │
│  Type:     source                             │
│  Created:  2026-05-08                         │
╰───────────────────────────────────────────────╯
╭─ Conflict ────────────────────────────────────╮
│  Existing note with same slug already in wiki │
│  → brain/wiki/sources/                        │
│    hook-adapter-testing-task-definition.md    │
│  Lifecycle: verified   Corroboration: 2       │
╰───────────────────────────────────────────────╯
╭─ Body Preview ────────────────────────────────╮
│  ## Summary                                   │
│  A defined task to test stop hook ingestion.  │
│  ...                                          │
╰───────────────────────────────────────────────╯
  [a] Approve  [r] Reject  [s] Skip  [↵] Details  [q] Quit
```

Colour scheme:
- `wiki_conflict` → yellow warning
- `type_collision` → red
- `low_confidence` → dim yellow
- `contested_lifecycle` → orange

### Conflict resolution display

For `wiki_conflict` and `type_collision`: query the `notes` table for a note
whose `note_id` matches `promotion_decisions.target_note_id` (or slug-match on
title) and surface its `lifecycle_state`, `corroboration_count`, and path.

```sql
SELECT note_id, title, lifecycle_state, path
FROM notes
WHERE note_id = ?
```

If found, show the conflict note's key fields side-by-side or below the review item.
If `target_note_id` is NULL (no DB record yet), derive the expected slug from the
review item's title and check if the file exists on disk.

### Keyboard bindings

| Key | Action |
|-----|--------|
| `a` | Approve with default lifecycle_state |
| `A` | Approve with prompt for lifecycle_state override |
| `r` | Reject |
| `s` or `→` | Skip (leave in review, move to next) |
| `←` | Go back to previous item |
| `↵` or `d` | Toggle full note view / preview |
| `q` or `Ctrl-C` | Quit (skipped items remain untouched) |

### Entry point

```python
def run_interactive_review(config: AppConfig) -> None:
    """Launch the interactive review TUI. Blocking until user quits."""
```

Uses `prompt_toolkit.input.create_input()` for raw single-key reads (no Enter
required). Falls back gracefully if TTY is not available (prints non-interactive
message, exits 1).

### Summary screen

After all items processed or user quits:

```
Review session complete.
  approved: 3   rejected: 2   skipped: 4
```

### CLI wiring

Change default behaviour of `mnemonic review` (no subcommand):

```python
# In _build_parser(): make review_command not required, default=None
review_subs = review_parser.add_subparsers(dest="review_command", required=False)

# In _review() handler: if sub is None → run_interactive_review(config)
```

Existing `list|show|approve|reject` subcommands unchanged.

## Milestones

1. `review_tui.py` scaffold: `ReviewSession` class, rich layout, static render of
   one item
2. Keyboard loop with `prompt_toolkit` raw input: `a/r/s/q/←/→/↵`
3. Conflict resolution: query notes table + disk fallback for slug check
4. `A` override path: inline prompt for lifecycle_state choice
5. Summary screen
6. CLI wiring: `required=False`, default to `run_interactive_review`
7. Tests (non-interactive): `run_interactive_review` with patched input stream
   to simulate keypress sequence

## Verification

```bash
# Launch interactive session against live review items
mnemonic review

# Simulated test: approve first, reject second, skip third, quit
python -m pytest tests/test_review_tui.py -v

# Full suite regression
python -m pytest tests/ -q   # 256+ pass
```

## Out of Scope

Mouse support, scrolling within body (truncate at N lines for preview), HTTP surface.

## Status

**Complete** (implemented 2026-05-08). 9/9 TUI tests pass. 265/265 full suite.

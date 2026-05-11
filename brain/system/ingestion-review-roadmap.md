# Ingestion: Review Command Roadmap

## Purpose

Records implementation progress against `ingestion-review-plan.md`.
Promotion layer routes low-confidence / conflicting candidates to `brain/wiki/review/`.
This roadmap tracks the command that lets humans act on those items.

## Verified Breakpoint (pre-implementation)

- 9 `review_routed` notes in `brain/wiki/review/` with no CLI to manage them.
- No `mnemonic review` command existed.
- No `src/second_brain/promotion/review_ops.py` existed.
- `promotion_decisions` rows with `promotion_state = 'review_routed'` had no
  automated path to `promoted` or `rejected`.
- Full test suite: 244 passing.

---

## Phase 1: Core Logic

Status: **complete** (2026-05-08).

Delivered:
- `src/second_brain/promotion/review_ops.py`
  - `ReviewItem` dataclass
  - `list_review_items(config)` — scans `wiki/review/*.md`, parses, sorts by `created_at`
  - `get_review_item(config, name)` — exact / `.md`-append / prefix match
  - `approve_review_item(config, name, *, lifecycle_state, dry_run)`
    - strips `## Promotion Review` header
    - sets `lifecycle_state` from `LIFECYCLE_DEFAULTS[type]` or override
    - atomic move to `wiki/{TYPE_TO_DIRECTORY[type]}/{slug}.md`
    - updates `promotion_decisions.promotion_state → 'promoted'`
    - calls `sync_vault(mode="incremental")`, appends `wiki/log.md`
    - raises `FileExistsError` on slug collision
  - `reject_review_item(config, name, *, dry_run)`
    - updates `promotion_decisions.promotion_state → 'rejected'`
    - deletes review file, appends `wiki/log.md`
- `tests/test_review_ops.py` — 12 tests, all passing

---

## Phase 2: Basic CLI

Status: **complete** (2026-05-08).

Delivered:
- `src/second_brain/cli.py` — `review` subparser with 4 sub-subcommands:
  - `review list [--json]` — numbered table: reason | type | title
  - `review show <name>` — prints raw file content
  - `review approve <name> [--lifecycle-state <state>] [--dry-run]`
  - `review reject <name> [--dry-run]`
- Handler `_review(config_path, args)` dispatches to `review_ops` functions
- Dispatch branch in `main()`: `if args.command == "review": return _review(args.config, args)`

Subcommands kept as non-interactive scriptable fallback for all future phases.

---

## Phase 3: Interactive TUI

Status: **complete** (2026-05-08).

Planned:
- `src/second_brain/promotion/review_tui.py`
  - `run_interactive_review(config)` — blocking TUI session
  - Rich panels per item: reason with colour coding, conflict note details,
    body preview (first ~10 lines)
  - For `wiki_conflict`/`type_collision`: queries `notes` table for conflicting
    note's `lifecycle_state`, `corroboration_count`, path; falls back to disk check
  - Keyboard bindings: `a`=approve, `A`=approve+override lifecycle, `r`=reject,
    `s`/`→`=skip, `←`=back, `↵`/`d`=full details, `q`/`Ctrl-C`=quit
  - Summary screen: approved / rejected / skipped counts
- `tests/test_review_tui.py` — simulated keypress tests
- CLI wiring: `mnemonic review` (no subcommand) → `run_interactive_review`

Dependencies: `rich` (already installed), `prompt_toolkit` (already installed).

---

## Phase 4: Hardening — Fix Loopholes

Status: **pending** (found 2026-05-09).

### Loophole 1 — `approve_review_item` atomicity

**File:** `src/second_brain/promotion/review_ops.py` (lines 139–162)

Current order: `write_text(final)` → `unlink(review)` → DB update.
DB failure after file ops leaves irrecoverable state.

Fix — reorder to: write tmp → DB update → `os.rename(tmp, final)` → `unlink(review)`.
On DB failure: `tmp.unlink(missing_ok=True)`, re-raise. Review file stays intact.
Add `import os` (not currently imported).

### Loophole 2 — `list_review_items` no filter

**File:** `src/second_brain/promotion/review_ops.py` (lines 38–67)

`_parse_review_file` does not validate the file is a promotion review.
19 of 21 files in `brain/wiki/review/` are maintenance artefacts
(`type: verification_review` / `merge_review`) — currently all pass the parser.

Fix — add two guards in `_parse_review_file` after frontmatter parse:
1. `if metadata.get("lifecycle_state") != "review": raise NoteParseError(...)`
2. `if not _REVIEW_HEADER_RE.search(body): raise NoteParseError(...)`

`list_review_items` already catches `NoteParseError` — no other changes needed.

### Tests to update/add

- `tests/test_review_ops.py` — existing fixture files must carry `lifecycle_state: review`
  and `## Promotion Review\nReason:` header or they will now be filtered out
- New test: `list_review_items` with mixed fixture dir → only promotion reviews returned
- New test: `approve_review_item` DB failure path → tmp cleaned up, review file intact

---

## Test Summary

`python -m pytest tests/test_review_ops.py`: **12 passed**

`python -m pytest tests/test_review_tui.py`: **9 passed**

`python -m pytest tests/`: **265 passed, 0 failed**

---

## Remaining Work

**Phase 4 (hardening)** — fix both loopholes above. See `ingestion-review-plan.md §Known Loopholes`.

**HTTP surface (out of scope for current plan):**

- `GET /v1/review` — JSON list
- `POST /v1/review/approve` / `POST /v1/review/reject`
- Low priority until there is a web UI or remote agent that would call these.

**Conflict merge (`--force` flag):**

When `approve_review_item` hits `FileExistsError` (slug collision), a `--force`
flag could run `renderer._render_existing` to merge the two notes. Deferred.

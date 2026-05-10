# Review Command Development Plan

## Purpose

Promotion layer routes low-confidence / conflicting candidates to `brain/wiki/review/`
as `lifecycle_state: review` markdown files. Without a dedicated command, human review
was entirely manual: open file in editor, move it, run sync by hand. This plan
specifies a `mnemonic review` command that makes human review fast and ergonomic.

---

## Glossary

| Term | Meaning |
|------|---------|
| review item | A `*.md` file in `brain/wiki/review/` with `lifecycle_state: review` and a `## Promotion Review` header |
| reason | Why it was routed to review: `wiki_conflict`, `type_collision`, `low_confidence`, `contested_lifecycle` |
| approve | Strip review header, advance lifecycle_state, move to final wiki dir, sync |
| reject | Delete review file, mark `promotion_decisions` row as `rejected` |
| conflicting note | An existing wiki note with the same slug or overlapping identity |

---

## Boundary

**In scope:**
- Listing, inspecting, approving, and rejecting review items
- Displaying context for each item: reason, conflicting notes, body preview
- Interactive terminal UI for sequential human review
- Non-interactive subcommands as scriptable fallback

**Out of scope:**
- Editing note content (user opens file in editor for that)
- Bulk merge logic (two notes with same slug → renderer `_render_existing` path)
- HTTP surface (`GET /v1/review`, `POST /v1/review/approve` etc.)
- Automated re-promotion without human confirmation

---

## Non-Negotiable Invariants

- `list_review_items` must filter strictly: only files where `lifecycle_state: review`
  AND body contains `## Promotion Review\nReason:` are valid review items.
  Files with `type: verification_review` or `type: merge_review` are maintenance
  artefacts and must never appear in the review queue.
- Approve is atomic: write to a `.tmp` file first, commit `promotion_decisions` DB
  update, then `os.rename(tmp → final)`, then delete review file, then `sync_vault`.
  If DB update fails, delete tmp and raise — review file stays intact for retry.
- Reject must never leave a dangling `promotion_decisions` row with `review_routed` state.
- Dry-run flag must prevent ALL side effects (no file moves, no DB writes, no sync).
- Interactive mode must be interruptible with `q` / `Ctrl-C` without corrupting state —
  skipped items stay in review dir untouched.

---

## High-Level Flow

```
mnemonic review                # interactive mode (Phase 3)
    ↓
list_review_items()            # scan wiki/review/*.md
    ↓
For each item (rich panel):
  show reason + conflicting note + body preview
  keyboard: a=approve  r=reject  s=skip  d=full details  q=quit
    ↓
approve_review_item()          # Phase 1 core logic
  strip header → set lifecycle → move file → update DB → sync_vault
```

---

## Phase Dependencies

```
Phase 1 (core logic)  →  Phase 2 (basic CLI)  →  Phase 3 (interactive TUI)
```

All phases are sequential. Phase 3 builds on Phase 1 functions; Phase 2 remains
as a scriptable non-interactive fallback.

---

## Out of Scope

- Web UI for review
- Agent-automated review (auto-approve based on confidence threshold) — would bypass
  the intent of human gating
- Batch conflict resolution merge

---

## Known Loopholes (found 2026-05-09)

Two bugs shipped in Phase 1 that violate the invariants above:

### Loophole 1 — `approve_review_item` atomicity broken

`review_ops.py` uses `final.write_text()` + `item.path.unlink()` before the DB
update.  If the DB update fails, the review file is already gone and the final file
is already written — no rollback is possible.

**Fix:** write to a `.tmp` file → DB update in `with conn:` → `os.rename(tmp, final)`
→ `item.path.unlink()`.  On DB failure: `tmp.unlink(missing_ok=True)`, re-raise.

### Loophole 2 — `list_review_items` scans all files, no filter

`list_review_items` globs every `*.md` in `brain/wiki/review/` and parses them all.
The directory contains 21 files; only 2 are promotion reviews.  The other 19 are
maintenance verification-review files (`type: verification_review` / `merge_review`)
written by automated jobs — they have no `lifecycle_state` field and no
`## Promotion Review` header, but they pass the current parser silently.

**Fix:** in `_parse_review_file`, guard early: raise `NoteParseError` if
`metadata.lifecycle_state != "review"` or `_REVIEW_HEADER_RE` does not match body.
`list_review_items` already catches `NoteParseError` and skips — no other call-site
changes needed.

---

## Readiness

Phase 1, 2, and 3 are complete (implemented 2026-05-08).
Phase 4 (hardening — fix loopholes above) is the active work item.

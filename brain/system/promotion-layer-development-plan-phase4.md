# Promotion Layer Phase 4: Wiki Writer

## Goal

Commit rendered notes to `brain/wiki/` atomically, record promotion decisions
in the ledger, and leave no partial state on failure.

## Context

This phase owns the only filesystem writes in the promotion layer. All earlier
phases are read-only or compute-only. The wiki writer is the enforcement point
for the rollback-safe and idempotent invariants.

## Scope

New module: `src/second_brain/promotion/writer.py`

New migration: `migrations/015_promotion_decisions.sql`

Updated `extraction_candidates.promotion_state` field written by this phase.

## Atomic Write Protocol

For each candidate that passes the gate and has a rendered note:

1. Compute target directory: `brain/wiki/{TYPE_TO_DIRECTORY[type]}/`
2. Compute target path: `{target_dir}/{slug}.md`
3. Ensure target directory exists (create if absent, idempotent).
4. Write rendered markdown to `{target_path}.tmp`.
5. Re-parse `.tmp` using `parser.py`. If parsing fails, go to rollback.
6. `os.rename(tmp_path, target_path)` — atomic on POSIX. If rename fails,
   go to rollback.
7. Append entry to `brain/wiki/log.md`.
8. Insert row into `promotion_decisions`.
9. Update `extraction_candidates.promotion_state` to `'promoted'` or
   `'review_routed'` (for `review` decisions).

Rollback path (triggered at step 5 or 6):
- Delete `.tmp` file if it exists.
- Do not write ledger row.
- Update `extraction_candidates.promotion_state` to `'rejected'`.
- Raise the exception so the daemon job can record it.

No step in the rollback path touches the final note path.

## Decision-Type Write Targets

| Decision | Target path |
|----------|-------------|
| `new` | `brain/wiki/{dir}/{slug}.md` |
| `confirm` | `brain/wiki/{dir}/{slug}.md` (overwrite with merged note) |
| `update` | `brain/wiki/{dir}/{slug}.md` (overwrite with merged note) |
| `review` | `brain/wiki/review/{slug}-{timestamp_utc}.md` |

## Idempotency

Before writing, check `promotion_decisions` for an existing row with the same
`candidate_id`. If one exists, skip all writes and return the existing decision.
This check uses the `UNIQUE INDEX` on `promotion_decisions.candidate_id`.

## Log Entry Format

Appended to `brain/wiki/log.md` after each successful write:

```
{ISO-8601 UTC} | {bridge_id} | {decision} | {note_id} | {reason}
```

This matches the existing log format observed in the file.

## Promotion Decisions Table

```sql
CREATE TABLE IF NOT EXISTS promotion_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES extraction_candidates(id),
    decision TEXT NOT NULL CHECK(decision IN ('new','confirm','update','review','rejected')),
    target_note_id TEXT,
    archive_id TEXT,
    check_errors_json TEXT NOT NULL DEFAULT '[]',
    promotion_state TEXT NOT NULL CHECK(promotion_state IN ('promoted','review_routed','rejected')),
    bridge_id TEXT NOT NULL DEFAULT 'job:process-promotion-queue',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS
    promotion_decisions_candidate_id ON promotion_decisions(candidate_id);
```

## Writer Interface

```python
def write_promotion(
    note_record: NoteRecord,
    rendered_markdown: str,
    *,
    decision: PromotionDecision,
    candidate_id: int,
    archive_id: str,
    bridge_id: str,
    conn: sqlite3.Connection,
    vault_root: Path,
) -> None:
    ...
```

Raises on rollback. Caller (daemon job) catches and records the error.

## Milestones

1. `migrations/015_promotion_decisions.sql` applies cleanly.
2. `writer.py` exists with `write_promotion()`.
3. Successful write lands note at correct path in `brain/wiki/`.
4. `promotion_decisions` row exists after successful write.
5. `extraction_candidates.promotion_state` updated to `'promoted'`.
6. `.tmp` file does not exist after successful write.
7. Parse failure at step 5 leaves wiki unchanged and marks candidate `rejected`.
8. Rename failure at step 6 leaves wiki unchanged and marks candidate `rejected`.
9. Re-running `write_promotion()` for same `candidate_id` is a no-op (idempotency).
10. `brain/wiki/log.md` contains a correctly formatted entry after write.

## Verification

- `new` decision creates file at `brain/wiki/{dir}/{slug}.md`.
- `confirm` decision overwrites file with merged confidence signal.
- `update` decision overwrites file with merged body and source_refs.
- `review` decision creates file at `brain/wiki/review/{slug}-{timestamp}.md`.
- Injecting a parse error after `.tmp` write leaves no file at final path.
- Injecting an OS error at `os.rename()` leaves no file at final path and
  deletes `.tmp`.
- `promotion_decisions` row is NOT created on rollback.
- `extraction_candidates.promotion_state` is `'rejected'` on rollback.
- Running the same candidate twice: second run returns without writing anything.

## Out Of Scope

- Daemon scheduling (Phase 5).
- CLI and HTTP admin surfaces (Phase 5).
- Sync and embedding backfill (triggered by the daemon job in Phase 5, not here).

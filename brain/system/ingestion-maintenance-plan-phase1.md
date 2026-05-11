# Knowledge Maintenance Phase 1: Foundation — corroboration_count in DB

> **Reclassified.** This work has been moved to the promotion layer and is now
> tracked as an amendment in `ingestion-promotion-roadmap.md`. The
> promotion layer owns the write path (`renderer.py` produces `corroboration_count`,
> `sync_vault` fires inline post-promotion) — only the DB projection was missing.
> This file is preserved as a reference for the implementation details.
>

## Goal

Make `corroboration_count` a first-class queryable field in the notes DB table,
mirroring its authoritative value in each note's markdown frontmatter. This is
purely additive — no behavior change to existing flows.

## Context

`corroboration_count` records how many independent extraction candidates have
confirmed or updated a given wiki note. The promotion renderer reads it from
the markdown frontmatter and increments it in place. No migration has ever
added it to the `notes` SQLite table.

Without it in the DB, the lifecycle audit (Phase 3) cannot query advancement
candidates — it would have to parse every markdown file on disk, which is slow
and fragile. With it in the DB, the query is a single SQL SELECT.

The migration runs automatically at startup via the existing `run_migrations`
call. All existing code that writes `notes` rows must be updated to include the
new column.

## Scope

### Migration: `migrations/018_corroboration_count.sql`

```sql
ALTER TABLE notes ADD COLUMN corroboration_count INT NOT NULL DEFAULT 0;
```

### `src/second_brain/models.py`

Add to `NoteRecord` dataclass (after `content_hash` field):

```python
corroboration_count: int
```

Default is 0 (used when constructing NoteRecord programmatically).

### `src/second_brain/parser.py` — `parse_note()` at line 187

Read `corroboration_count` from frontmatter metadata dict. If absent or not an
integer, default to 0.

```python
corroboration_count = int(metadata.get("corroboration_count") or 0)
```

Pass into the `NoteRecord(...)` constructor call at the end of `parse_note`.

### `src/second_brain/sync.py` — `_upsert_note()` at line 79

Add `corroboration_count` to:
1. The column list in the `INSERT INTO notes(...)` statement
2. The `VALUES(...)` positional parameter list
3. The `ON CONFLICT(id) DO UPDATE SET corroboration_count = excluded.corroboration_count` clause

The value is taken from `note.corroboration_count`.

**Important:** the existing `ON CONFLICT` clause uses `revision = notes.revision + 1`
to track write count. `corroboration_count` is separately sourced from frontmatter
and must not be conflated with `revision`. It should be an independent UPDATE SET
field.

## What does NOT change

- `promotion/renderer.py` — continues to write `corroboration_count` into the
  markdown frontmatter exactly as today. The DB column is populated on the next
  `sync_vault` call, not by the renderer directly. The renderer does not need to
  know about the DB column.
- `promotion/writer.py` — no change. The existing post-write `sync_vault` call
  (triggered by `trigger_pipeline`) will pick up the new column automatically.
- The lifecycle advancement thresholds (`corroboration_count >= 2` for verified,
  `>= 4` for canonical) remain defined in `promotion/renderer.py` and are
  re-used by Phase 3.

## Milestones

1. Migration file exists and applies cleanly against an existing DB.
2. `NoteRecord.corroboration_count` field exists.
3. `parse_note()` reads `corroboration_count` from frontmatter; absent → 0.
4. `_upsert_note()` writes `corroboration_count` to the DB.
5. `sync_vault()` on a note with `corroboration_count: 3` in frontmatter → DB row
   shows 3.
6. `sync_vault()` on a note with no `corroboration_count` in frontmatter → DB row
   shows 0.

## Verification

- Test: round-trip a note file with `corroboration_count: 3` through `parse_note`
  → `_upsert_note` → SELECT → confirm DB value is 3.
- Test: round-trip a note with no `corroboration_count` frontmatter key → DB value
  is 0.
- Test: migration applies to a DB created by migrations 001–017 without error.
- Existing test suite (244 tests) still passes.

## Out of scope

- Backfilling DB values for existing notes (sync_vault handles this on next run).
- Using `corroboration_count` for any decision logic (Phase 3).
- Any change to how the renderer writes `corroboration_count` to frontmatter.

# Knowledge Maintenance Phase 3: Lifecycle Auditing — Advancement and Drift

## Goal

Scan all active wiki notes daily and advance lifecycle states (draft → verified →
canonical) when corroboration thresholds are met, without waiting for new
promotions. Flag notes that have stalled in `draft` without corroboration.

## Context

Lifecycle advancement currently happens only inside `promotion/renderer.py` when a
new candidate is promoted. If a note has been corroborated enough times but no new
candidates arrive, its lifecycle never advances. The `corroboration_count` field
accumulates in the frontmatter correctly, but nothing reads it back to drive state
transitions outside of the promotion path.

Phase 1 puts `corroboration_count` in the DB. Phase 3 queries it to find
advancement candidates and drift candidates, then writes back to both the
frontmatter file and the DB.

The advancement thresholds mirror `promotion/renderer.py`:
- `draft` → `verified`: `corroboration_count >= 2`
- `verified` → `canonical`: `corroboration_count >= 4`

## Scope

### New config section: `MaintenanceConfig` in `src/second_brain/config.py`

```python
@dataclass(slots=True)
class MaintenanceConfig:
    lifecycle_verified_threshold: int = 2
    lifecycle_canonical_threshold: int = 4
    lifecycle_drift_days: int = 30
    graph_rebuild_enabled: bool = True
    lifecycle_audit_enabled: bool = True
    importance_scoring_enabled: bool = True
    semantic_dedup_threshold: float = 0.92
    semantic_dedup_enabled: bool = True
```

Wire into `AppConfig` as `maintenance: MaintenanceConfig`. Parse from `[maintenance]`
section of `config/memory.toml`. All fields optional with the defaults above.

### `LifecycleAuditResult` dataclass in `maintenance.py`

```python
@dataclass(slots=True)
class LifecycleAuditResult:
    notes_scanned: int
    advanced_to_verified: int
    advanced_to_canonical: int
    drift_flagged: int
```

### `audit_lifecycle_states(connection, config, *, dry_run=False) -> LifecycleAuditResult`

Algorithm:

**Advancement pass:**

1. Query: `SELECT id, body_path, lifecycle_state, corroboration_count, created_at
   FROM notes WHERE status IS NULL OR status NOT IN ('retired', 'archived', 'superseded')`.
2. For each row, determine target lifecycle:
   - `lifecycle_state == 'draft'` AND `corroboration_count >= lifecycle_canonical_threshold` → `'canonical'`
   - `lifecycle_state == 'draft'` AND `corroboration_count >= lifecycle_verified_threshold` → `'verified'`
   - `lifecycle_state == 'verified'` AND `corroboration_count >= lifecycle_canonical_threshold` → `'canonical'`
   - Otherwise: no change.
3. If target != current and not dry_run:
   a. Read the markdown file at `body_path`.
   b. Parse YAML frontmatter (re-use the raw yaml.safe_load approach, not
      `parse_note` — we only need the frontmatter dict, not a full NoteRecord).
   c. Set `lifecycle_state` in the frontmatter dict to the target value.
   d. Re-render frontmatter + body via `_render_note_md` pattern from
      `promotion/renderer.py` (extract this helper to a shared location, or
      inline the yaml.safe_dump approach).
   e. Write back atomically: write `.tmp`, rename.
   f. UPDATE notes SET lifecycle_state = ? WHERE id = ? (direct SQL, no
      full re-sync).
4. Increment `advanced_to_verified` or `advanced_to_canonical` as appropriate.

**Drift pass:**

5. Query: `SELECT id, title, type, body_path, created_at FROM notes WHERE
   lifecycle_state = 'draft' AND (corroboration_count IS NULL OR corroboration_count = 0)
   AND status IS NULL`.
6. For each row where `days_since(created_at) > config.maintenance.lifecycle_drift_days`:
7. If not dry_run: call `create_review_item(config, review_type="lifecycle_review",
   suggested_action="investigate or archive stalled draft", ...)`.
8. Increment `drift_flagged`.

**Wire into `daily_consolidation`:**

```python
if config.maintenance.lifecycle_audit_enabled:
    audit_result = audit_lifecycle_states(connection, config, dry_run=dry_run)
    stats["lifecycle_advanced_to_verified"] = audit_result.advanced_to_verified
    stats["lifecycle_advanced_to_canonical"] = audit_result.advanced_to_canonical
    stats["lifecycle_drift_flagged"] = audit_result.drift_flagged
```

Call after `rebuild_wiki_graph`, before trust/freshness.

## YAML frontmatter write-back approach

Do not call `parse_note` + `sync_vault` for lifecycle advancement. That is too
heavy (re-reads and re-inserts the entire note). Instead:

1. `raw = path.read_text(encoding="utf-8")`
2. Split at `---` fence to isolate frontmatter and body.
3. `metadata = yaml.safe_load(frontmatter_text)`
4. `metadata["lifecycle_state"] = target_state`
5. Re-serialize with `yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False)`
6. Write tmp file → `os.rename()`.
7. UPDATE notes DB row directly.

This is the same pattern used in `promotion/renderer.py:_render_note_md`. If that
function or its YAML serialization logic is extracted to a shared utility (e.g.
`src/second_brain/noteio.py`), use it here. Otherwise inline the pattern cleanly.

## Edge cases

- Note body_path does not exist on disk: log warning, skip, do not update DB.
- Note lifecycle_state is `review`, `contested`, or `archived`: skip — these
  states are not eligible for automatic advancement.
- `corroboration_count` in DB is 0 but frontmatter has a higher value: trust the
  DB (Phase 1 sync keeps them consistent); if they diverge, the next `sync_vault`
  pass will correct DB from frontmatter.
- Multiple notes at `drift` threshold: each creates a separate review item.
  Deduplication is handled by `create_review_item`'s `slug_seed` parameter.

## Milestones

1. `MaintenanceConfig` exists in `config.py`; `AppConfig.maintenance` is populated.
2. `LifecycleAuditResult` dataclass exists in `maintenance.py`.
3. `audit_lifecycle_states` advances `draft` notes with sufficient corroboration.
4. Both frontmatter file and DB row are updated atomically.
5. Notes with `lifecycle_state` in `{'review', 'contested', 'archived'}` are skipped.
6. Stalled draft notes beyond `lifecycle_drift_days` generate review items.
7. `daily_consolidation` calls `audit_lifecycle_states`; stats dict includes
   lifecycle keys.
8. Dry-run produces no disk writes, no DB writes, no review items.

## Verification

- Test: note with `lifecycle_state: draft`, `corroboration_count: 2` → after
  audit, frontmatter shows `lifecycle_state: verified`, DB row updated.
- Test: note with `lifecycle_state: draft`, `corroboration_count: 4` → advances
  directly to `canonical` (skips intermediate verified step).
- Test: note with `lifecycle_state: verified`, `corroboration_count: 4` → advances
  to `canonical`.
- Test: note with `lifecycle_state: contested` → unchanged.
- Test: note with `lifecycle_state: draft`, `corroboration_count: 0`, old
  `created_at` → review item created.
- Test: `dry_run=True` → no file writes, no DB writes, no review items.
- Integration: `daily_consolidation` dry-run with lifecycle_audit_enabled=True
  completes without error.

## Out of scope

- Downgrading lifecycle states (e.g., canonical → verified). Downgrade happens
  via human review of contradiction items, not automated maintenance.
- `contested` state management — handled by existing contradiction detection and
  review ops.
- Bulk re-sync via `sync_vault` — the direct DB update is sufficient and faster.

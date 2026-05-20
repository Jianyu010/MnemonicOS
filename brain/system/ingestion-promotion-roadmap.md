# Ingestion: Promotion Roadmap

## Purpose

This roadmap records implementation progress against
`ingestion-promotion-plan.md`, what remains, and what the current layer
can claim.

## Verified Breakpoint (pre-implementation)

- Full repository test suite: 198 tests passing.
- `extraction_candidates` rows with `promotion_state = 'promotion_ready'` were
  produced by the extraction worker but not consumed by any wiki-writing layer.
- No `src/second_brain/promotion/` package existed.
- No `migrations/015_promotion_decisions.sql` existed.
- No `process_promotion_queue` daemon job was registered.

## Phase 1: Promotion Gate

Status: **complete**.

Delivered:
- `src/second_brain/promotion/gate.py` — `run_gate(payload, *, config) -> GateResult`
- `src/second_brain/promotion/models.py` — `GateResult`, `PromotionDecision`, `PromotionRunResult`
- 7 deterministic checks (policy gate, type vocab, source evidence, trace/component
  ID validity, component links, trace boundary grounding, component count limit)
- Imports private helpers from `extraction/validation.py` — no duplication
- Check 7 (policy disabled) fires first with early return; all other checks collect
  errors before returning (no short-circuit)

Tests: 9 gate tests, all passing.

## Phase 2: Decision Engine

Status: **complete**.

Delivered:
- `src/second_brain/promotion/decisions.py` — `decide(candidate, *, duplicate_state, conn, config) -> PromotionDecision`
- 8 rules evaluated in order: low-confidence guard → type collision → no match →
  contested lifecycle → conflict → near-duplicate prior → strong match confirm/update → fallback
- Rule 0: `canonical_duplicate`/`lsh_duplicate` + strong match (score ≥ 0.90) →
  always `confirm` (same source bytes, body novelty check skipped)
- Uses `find_existing_candidates` from `bridge.py` directly

Tests: 4 decision tests, all passing.

## Phase 3: Note Renderer

Status: **complete**.

Delivered:
- `src/second_brain/promotion/renderer.py` — `render(candidate, *, decision, candidate_id, archive_id, existing_note_path) -> (dict, str)`
- `_render_new()`: lifecycle_state from `LIFECYCLE_DEFAULTS`, corroboration_count=1,
  chain_id from single trace, provenance refs appended
- `_render_existing()`: increments corroboration_count; advances lifecycle_state
  (draft→verified at ≥2, verified→canonical at ≥4); merges source_refs;
  confirm=preserve body, update=append new body
- Review routing: prepends `## Promotion Review` header, sets lifecycle_state="review"

Tests: 11 renderer tests, all passing.

## Phase 4: Wiki Writer

Status: **complete**.

Delivered:
- `src/second_brain/promotion/writer.py` — atomic write protocol (tmp → parse
  smoke check → `os.rename()` → log → ledger → update extraction_candidates)
- `write_rejected()` — records gate failures without wiki write
- `_append_log()` — appends ISO-8601 UTC line to `wiki/log.md`
- `migrations/014b_extraction_candidates_reshape.sql` — bridges live DB old schema
  (`candidate_payload_json`, no `candidate_index`) to new schema before migration 015
- `migrations/015_promotion_decisions.sql` — expands `extraction_candidates`
  `promotion_state` CHECK constraint, creates `promotion_decisions` table, unique
  index on `candidate_id`
- On any exception after `.tmp` write: deletes `.tmp`, marks rejected, re-raises

Tests: 8 writer tests, all passing.

## Phase 5: Daemon and Admin Surfaces

Status: **complete except stuck-row recovery and explicit embed_backfill dirty marking**.

Delivered:
- `src/second_brain/promotion/worker.py` — `process_promotion_queue(config, *, dry_run=False)`
  full pipeline: json.loads → gate → decide → render → write_promotion; triggers
  `sync_vault(mode="incremental")` after batch when notes were promoted
- `src/second_brain/config.py` — `PromotionConfig` dataclass + `[promotion]` section parsing
- `src/second_brain/daemon_jobs.py` — `"process_promotion_queue": "*/5 * * * *"` schedule
- `src/second_brain/jobs.py` — `_process_promotion_queue_job()`, `run_job()` branch,
  `promotion_status()` returning ready/promoted/review_routed/rejected/last_run_at;
  `collect_status_snapshot()` includes `promotion` key
- `src/second_brain/cli.py` — `process_promotion_queue` added to `run-job` choices;
  `promote-status` sub-command added (calls `promotion_status()`, supports `--json`)
- `src/second_brain/server.py` — `POST /v1/promotion/process` and
  `GET /v1/promotion/status` routes registered
- `src/second_brain/daemon_status.py` — Promotion Queue section in status summary
  (ready, promoted, review, rejected, last run, next run)
- `config/memory.example.toml` — `[promotion]` section and
  `process_promotion_queue = "*/5 * * * *"` in `[daemon.schedules]`

Tests: 8 worker tests, all passing.

## Test Summary

`python -m pytest tests/test_promotion_layer.py`: **43 passed, 4 subtests passed**

`python -m pytest tests/`: **244 passed, 0 failed** (all tests green, including
`test_hook_adapter.py` — the migration compatibility fix resolved the 2 previously
failing hook adapter tests).

## Phase 5 Gap Closure

Status: **complete** — see `ingestion-promotion-plan-phase5-gaps.md`.

Two gaps identified after Phase 5 landed:

### Gap 1: Stuck-row recovery

`write_rejected` sets both `extraction_candidates.promotion_state = 'rejected'`
and a `promotion_decisions` row with `promotion_state = 'rejected'`. The main
worker fetch filters on `promotion_state = 'promotion_ready'` with `NOT EXISTS`
on `promotion_decisions` — rejected candidates permanently fail both checks.

Fix: add requeue sweep at start of `process_promotion_queue`. Before the main
fetch, UPDATE `extraction_candidates.promotion_state` back to `'promotion_ready'`
and DELETE stale `promotion_decisions` rows older than `retry_after_hours` (default
24h). Add `requeued: int = 0` to `PromotionRunResult`.

### Gap 2: Post-promotion sync coverage

`sync_vault` (which writes `notes` and `note_vectors` inline) is called only from
`_process_promotion_queue_job` in `jobs.py`. When the worker runs inline via
`trigger_pipeline`, no sync runs. `review_routed` path never triggers sync at all.

Fix:
- Move `sync_vault` call into `process_promotion_queue` worker itself. Triggers on
  `promoted > 0 OR review_routed > 0`. Remove redundant call from `jobs.py` wrapper.
- Add `trigger_embed_backfill(config)` to `pipeline.py`. It runs through the
  daemon when reachable and falls back inline otherwise. Call after `sync_vault`
  when `promoted > 0`. Closes the archive-chunk-vector gap promptly.

## Amendment: corroboration_count in DB

Status: **not started**.

`renderer.py` already writes `corroboration_count` to markdown frontmatter at promotion
time. `sync_vault(mode="incremental")` already fires inline after every promotion batch.
But `NoteRecord`, `parser.py`, and `_upsert_note` in `sync.py` do not carry the field,
so the DB value is always absent.

This amendment closes the gap. It belongs to the promotion layer — not the maintenance
layer — because the write path already produces the correct value; only the DB projection
is missing. With this change, `corroboration_count` is written to the DB within the same
promotion run, with no deferred reload.

Planned deliverables (see `ingestion-maintenance-plan-phase1.md` for full
scope; that file is preserved as reference but the work is owned here):

- `migrations/018_corroboration_count.sql` — `ALTER TABLE notes ADD COLUMN corroboration_count INT NOT NULL DEFAULT 0`
- `models.py` — `corroboration_count: int` field added to `NoteRecord`
- `parser.py` — parses `corroboration_count` from frontmatter; absent → 0
- `sync.py` — `_upsert_note` includes `corroboration_count` in INSERT and ON CONFLICT UPDATE

No change to `renderer.py` or `writer.py`. The existing `sync_vault(mode="incremental")`
call in `process_promotion_queue` carries the new field automatically once `_upsert_note`
is updated.

Maintenance-layer jobs that detect a new corroboration during a maintenance pass write
back to both the markdown frontmatter and the DB via the same `_upsert_note` path — this
is the only case where the maintenance layer touches `corroboration_count`.

## Amendment: Intra-Source Relation Application

Status: **not started**. Requires LLM/VLM Phase 8 to ship first.

Planned deliverables (see
`ingestion-promotion-plan-intra-source-relations.md`):

- `PromotionRunResult.relations_applied: int` field.
- `apply_relation_to_note(note_path, *, target_note_id, relation_type) -> bool`
  — atomic frontmatter append + idempotent dedup.
- `_apply_intra_source_relations(config, connection, promoted_by_queue)` in
  `promotion/worker.py` — bundle-aware second pass after candidate loop.
- Worker collects `promoted_by_queue: dict[queue_id, dict[title, note_id]]` during
  the per-candidate loop.
- After candidate loop: loads `intra_source_relations_json` per queue row, resolves
  titles to note IDs, calls `apply_relation_to_note`, triggers
  `rebuild_note_graph` for each updated note.
- Skip conditions: target or source review-routed/rejected, file missing, relation
  already present.
- Dry-run: no frontmatter writes, no graph rebuild.

This amendment completes the intra-source graph edge pipeline:

```text
LLM bundle → intra_source_relations_json (Phase 8)
    → promotion candidate loop (per-candidate, existing)
    → bundle-aware second pass (this amendment)
    → apply_relation_to_note → frontmatter update → rebuild_note_graph
    → graph_edges rows for same-source note pairs
```

## Remaining Work

**Integration test gaps:**

- `confirm` and `update` paths with real existing note files on disk (renderer
  corroboration logic is unit-tested; end-to-end confirm/update filesystem
  round-trip is not).

**Pending amendment:**

- Intra-source relation application (blocked on LLM/VLM Phase 8).

---

## Phase 6: Observation Model, Time-Weighted Conflict Resolution, Human Review Elimination

Status: **not started**.

Plan: `ingestion-promotion-plan-phase6.md`.

### Summary

Replaces the current `confirm`-as-counter and `wiki_conflict`-routes-to-human
behaviour with a principled model: raw observations are immutable, notes morph.

**Observe-and-relate model (replaces `confirm`):**
- Every non-conflict match promotes the new candidate as a standalone observation note.
- New note gets `corroborates → old_note_id` relation.
- Old note: `last_seen` refreshed, `corroboration_count++`, body merged if new
  content, confidence raised if new confidence is higher.
- `confirm` and `update` collapse into the same handler; only body-merge extent differs.

**New relation type `corroborates`:**
- Direction: new → old.
- Added to `RELATION_BUCKETS` as `"corroboration"` bucket.
- `recall.py` auto-generates `reverse_corroborates` (old → new).

**Time-weighted conflict resolution (replaces `wiki_conflict` → human):**
- Deterministic table based on `new_confidence`, `old_confidence`, and `time_gap_days`.
- Config: `conflict_stale_threshold_days` (default 30), `conflict_glitch_threshold_days` (default 1).
- Large gap + conflicting → old becomes `stale`; small gap → old becomes `contested`.
- New `low_confidence` + any old → promote new as `contested`, old untouched.

**Human review elimination:**
- `low_confidence` → auto-reject in `auto_resolver.py` before LLM step.
- `wiki_conflict` and `contested_lifecycle` → time-weighted auto-resolve, never routed to human.
- LLM arbitration fallback (< threshold confidence) → auto-reject, not human queue.
- `type_collision` **removed**: same slug + different type = different note ID, no real collision.
  Risk analysis (retrieval noise, type error decay, generic title flood) accepted — see plan for detail.
  Human review queue is **empty under normal operation** after Phase 6.

**`rejection_reason` column:**
- `migrations/030_rejection_reason.sql` — `ALTER TABLE extraction_candidates ADD COLUMN rejection_reason TEXT`.
- Structured reasons: `low_confidence_extraction`, `gate_failure:<check>`, `type_collision_llm_uncertain`.

### Deliverables

- `src/second_brain/promotion/decisions.py` — remove `wiki_conflict` / `contested_lifecycle` as `review` outcomes
- `src/second_brain/promotion/auto_resolver.py` — short-circuit `low_confidence`; replace human fallback with auto-reject
- `src/second_brain/promotion/renderer.py` — observe-and-relate for `confirm`/`update`; write `corroborates` relation; refresh old note timestamp and confidence
- `src/second_brain/ingest.py` — add `corroborates` to `RELATION_BUCKETS`
- `src/second_brain/config.py` — add two `PromotionConfig` threshold fields
- `src/second_brain/promotion/review_ops.py` and `writer.py` — pass `rejection_reason` through
- `migrations/030_rejection_reason.sql`

## Amendment: Passive Attribution Ledger Write

Status: **complete 2026-05-13**.

Owner plan: `ingestion-promotion-plan-phase4.md` — "Passive Attribution Ledger Write" section.

When a promoted candidate's payload contains a `retrieval_attribution` array
(extracted by the LLM during session extraction per extraction Phase 9), the wiki
writer stores it as `promotion_decisions.retrieval_attribution_json`. This column
is the handoff to the maintenance layer's passive feedback derivation job.

Planned deliverables:

- `migrations/026_passive_feedback.sql` — adds `retrieval_attribution_json TEXT`
  column to `promotion_decisions`; creates `note_passive_feedback` table.
- `src/second_brain/promotion/writer.py` — extend `write_promotion()` to accept
  `retrieval_attribution_json: str | None` and store it in the ledger row.
- `src/second_brain/promotion/worker.py` — extract `retrieval_attribution` from
  `payload["retrieval_attribution"]` (if present) and pass serialized to writer.
- Tests: promotion with `retrieval_attribution` in payload → ledger row has
  non-null `retrieval_attribution_json`.

Constraints:
- Does not modify the wiki file of any note referenced in `retrieval_attribution`.
- Does not write `note_passive_feedback` — that is maintenance-owned.
- Stored for all decision types (`new`, `confirm`, `update`, `review`).
- Absence of field in payload → null in ledger (no error, no warning).

---

## Architectural Conclusion

The promotion layer is operational. The two Phase 5 gaps (stuck-row recovery, post-
promotion sync coverage) are planned in `ingestion-promotion-plan-phase5-gaps.md`.
The intra-source relation amendment (planned in
`ingestion-promotion-plan-intra-source-relations.md`) adds bundle-aware
post-processing to write LLM-asserted relations between same-source notes to their
frontmatter and rebuild their graph edges. This closes the primary graph edge gap
for notes produced through the automated pipeline.

The passive attribution amendment closes the feedback loop: extracted sessions that
include retrieved notes now carry attribution evidence through to the promotion ledger,
where the maintenance passive feedback derivation job can consume it without a
separate LLM pass.

MnemonicOS full pipeline after all amendments ship:

```text
west flange
    → LLM/VLM extraction (candidates + intra_source_relations + retrieval_attribution)
    → promotion gate → decision engine → note renderer → atomic wiki write
    → bundle-aware second pass → apply_relation_to_note → rebuild_note_graph
    → store retrieval_attribution_json in promotion_decisions
    → sync_vault (writes corroboration_count + all note fields to DB) + embed_backfill
    → knowledge maintenance layer:
        derive_passive_feedback (inline + post-hoc paths)
        → note_passive_feedback → note_trust / note_freshness updates
    → (retrieval Stage 3 reads note_passive_feedback for NoteSignals)
```

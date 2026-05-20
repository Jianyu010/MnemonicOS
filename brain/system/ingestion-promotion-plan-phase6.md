# Promotion Layer Phase 6: Observation Model, Time-Weighted Conflict Resolution, and Human Review Elimination

## Goal

Replace the current `confirm`-as-counter and `wiki_conflict`-routes-to-human behaviour
with a principled observation model: notes morph, raw observations are immutable.
Eliminate the human review queue for all cases except genuine type slug collisions.

## Principles

**Raw observations are immutable.** Each extraction candidate is a verbatim read of
one raw session. It must never be silently dropped or mutated to match existing wiki
state. It is always promoted as a standalone observation record.

**Notes are the canonical knowledge holder and can morph.** A wiki note is the
current best synthesis over all corroborating observations. New observations update
the note's timestamp, may extend its body, and track their provenance via relations.

**Freshness is time-stamped, not counted.** `corroboration_count` is a secondary
signal; the primary freshness driver is `last_seen` — the timestamp of the most
recent corroborating observation. This is what the time-weight conflict formula uses.

**New evidence always respects the wiki.** Even a low-confidence observation is
promoted and recorded; it just does not overrule a high-confidence note.

---

## Changes to `decide()` and the Promotion Pipeline

### Unified Observe-and-Relate model

Current `confirm` decision: increment `corroboration_count` on old note, no new note
promoted, no timestamp update, no relation written. **This is wrong** under the new
model.

New behaviour for every non-conflict match (was `confirm` or `update`):

1. Promote new candidate as its own wiki note (observation record).
2. Write relation `corroborates → old_note_id` on new note.
3. On old note: refresh `last_seen` to new note's `created_at`; increment
   `corroboration_count`; if new body has non-duplicate content, merge it into old;
   if new confidence is higher, raise old note's confidence.

`confirm` vs `update` distinction collapses: both paths promote new, write
`corroborates`, and potentially update old body. The only difference is how much body
is merged (confirm = minimal / additive, update = full append). The decision engine
may keep both labels for stats, but the handler is the same.

### New relation type: `corroborates`

Add to `RELATION_BUCKETS` in `src/second_brain/ingest.py`:

```python
"corroborates": "corroboration",
```

Direction: **new note → old note**. Semantics: "this newer observation corroborates
the older canonical note." `recall.py` auto-generates `reverse_corroborates` on the
old note (old → new) via the existing `reverse_` prefix pattern.

This gives the old note a queryable list of all observations that back it up, ordered
by timestamp — the full corroboration chain.

---

## Time-Weighted Conflict Resolution

Replaces `wiki_conflict` → human review. No LLM required. Deterministic logic based
on candidate confidence, note confidence, and time gap.

### Inputs

- `new_confidence`: extracted from candidate payload (`"low"` / `"medium"` / `"high"`)
- `old_confidence`: from existing note frontmatter
- `time_gap_days`: `(candidate.created_at - old_note.last_seen).days`
- `stale_threshold_days`: `config.promotion.conflict_stale_threshold_days` (default: 30)
- `glitch_threshold_days`: `config.promotion.conflict_glitch_threshold_days` (default: 1)

Confidence rank: `high=2`, `medium=1`, `low=0`.

### Resolution table

| New conf | Old conf | Time gap | Action |
|---|---|---|---|
| high | high | > stale_threshold | Promote new; `supersedes → old`; old → `lifecycle=stale`, `confidence=low` |
| high | high | ≤ stale_threshold, > glitch | Promote new; `supersedes → old`; old → `lifecycle=contested`, confidence −1 level |
| high | high | ≤ glitch_threshold | Promote new; `corroborates → old`; old → `lifecycle=contested`; no confidence change |
| high | medium | any | Promote new; `supersedes → old`; old confidence −1 level; lifecycle = `contested` |
| high | low | any | Promote new; `supersedes → old`; old → `lifecycle=archived` |
| medium | high | any | Promote new; `corroborates → old`; old untouched |
| medium | medium | > stale_threshold | Promote new; `supersedes → old`; old → `lifecycle=stale` |
| medium | medium | ≤ stale_threshold | Promote new; `corroborates → old`; old untouched |
| medium | low | any | Promote new; `supersedes → old`; old → `lifecycle=archived` |
| low | any | any | Promote new; mark new `lifecycle=contested`; old untouched |

Confidence −1 level: `high→medium`, `medium→low`. Does not go below `low`.

Old note mutations (lifecycle/confidence changes) are applied as frontmatter patches
via `apply_relation_to_note`-style atomic writes, then synced to DB via
`sync_vault(mode="incremental")`.

### Config additions

```toml
[promotion]
conflict_stale_threshold_days = 30
conflict_glitch_threshold_days = 1
```

---

## Elimination of Human Review for Most Cases

### `low_confidence` → auto-reject (bypass LLM arbitration)

In `_process_one` in `auto_resolver.py`, before the LLM step:

```python
if decision.reason == "low_confidence":
    if not dry_run:
        _safe_reject(config, item.filename, reason="low_confidence_extraction")
    result.rejected += 1
    return
```

Rationale: extraction LLM already declared weak evidence. A second LLM pass adds
cost without changing the signal. Reject and record.

### `wiki_conflict` and `contested_lifecycle` → time-weighted auto-resolve

These no longer route to the review queue at all. `decide()` emits them only when
the time-weight logic cannot deterministically pick a winner (e.g., equal confidence,
small gap, genuinely indeterminate). In practice, the resolution table above covers
all cases deterministically. The `review` decision for these reasons should be
removed from `decide()` output.

### LLM arbitration fallback → auto-reject (not human queue)

When LLM arbitration runs (currently only for `type_collision`) and returns
confidence < `_CONFIDENCE_THRESHOLD` or `action = "human"`, replace:

```python
# Old
_append_llm_analysis(item, llm_result)
result.human_fallback += 1
```

with:

```python
_safe_reject(config, item.filename, reason=f"{decision.reason}_llm_uncertain")
result.rejected += 1
```

### `type_collision` removed

The `type_collision` check in `decide()` is removed entirely. Rationale:

Note IDs include the type prefix (`concept/foo`, `procedure/foo`) — different IDs,
different filesystem paths, no actual collision. The check was conservative theater.

**Risk analysis (accepted):**

- *Retrieval noise*: same slug under two types produces two notes in search results.
  `find_existing_candidates` filters by `note_type`, so they never corroborate or
  conflict each other. Treated as independent notes — same as any two topically
  adjacent notes. Acceptable.

- *Type error accumulates silently*: extraction LLM miscategorizes a procedure as
  concept. Promoted as `concept/foo`. Future revisits either re-extract as `concept`
  (corroborate, no harm) or correctly as `procedure` (now `procedure/foo` exists;
  `concept/foo` stops getting corroborated, freshness decays, goes stale). Self-healing
  without human intervention.

- *Generic title flood*: "Configuration" extracted under 5 types. Each has a different
  body; different IDs, different directories, different retrieval context. Not duplicates.

- *Symmetric type disagreement*: same semantic content extracted with two types
  consistently across sessions — both notes accumulate corroboration, neither decays.
  This is a signal the extraction prompt needs type-guidance fixes, not a promotion
  problem. Acceptable at this layer.

**Consequence**: `wiki_auto_resolve_review` LLM arbitration for `type_collision`
is also removed. No human fallback needed.

### Net result

Human review queue is **empty under normal operation**. All promotion paths are
deterministic. The `brain/wiki/review/` directory and `mnemonic review` CLI remain
available for manual use but receive no automated routing.

---

## DB Changes

### `rejection_reason` column

```sql
ALTER TABLE extraction_candidates ADD COLUMN rejection_reason TEXT;
```

Migration: `migrations/030_rejection_reason.sql`.

Populate in `reject_review_item` and `write_rejected` with structured reason strings:
- `low_confidence_extraction`
- `gate_failure:<check_name>`
- `type_collision_llm_uncertain`
- `wiki_conflict_unresolved` (should become rare)

Enables audit queries:
```sql
SELECT rejection_reason, COUNT(*) FROM extraction_candidates
WHERE promotion_state = 'rejected'
GROUP BY rejection_reason ORDER BY COUNT(*) DESC;
```

---

## Scope

Modified:
- `src/second_brain/promotion/decisions.py` — remove `type_collision`, `wiki_conflict`,
  and `contested_lifecycle` as `review` outcomes; wire conflict to time-weighted resolution
- `src/second_brain/promotion/auto_resolver.py` — short-circuit `low_confidence`,
  replace human fallback with auto-reject; remove `type_collision` LLM resolver
- `src/second_brain/promotion/renderer.py` — implement observe-and-relate for
  `confirm` and `update` paths; write `corroborates` relation; refresh old note
  timestamp and confidence
- `src/second_brain/ingest.py` — add `corroborates` to `RELATION_BUCKETS`
- `src/second_brain/config.py` — add `conflict_stale_threshold_days`,
  `conflict_glitch_threshold_days` to `PromotionConfig`
- `src/second_brain/promotion/review_ops.py` — pass `rejection_reason` through
  `reject_review_item`
- `src/second_brain/promotion/writer.py` — pass `rejection_reason` to
  `write_rejected`; store in DB

New:
- `migrations/030_rejection_reason.sql`

---

## Acceptance Criteria

- `confirm` path: promotes new note, writes `corroborates` relation, refreshes old
  note `last_seen`, increments `corroboration_count` on old.
- `update` path: same as confirm plus body merge into old note.
- `low_confidence` items: never enter auto_resolver LLM path; land in
  `extraction_candidates.rejection_reason = 'low_confidence_extraction'`.
- `wiki_conflict` with high/high conf, gap > 30d: new promoted, old `stale`.
- `wiki_conflict` with low conf new: new promoted `contested`, old untouched.
- `type_collision` check absent from `decide()`: same-slug different-type candidates promoted without review.
- Human fallback count is zero for all items. `human_fallback` stat always 0.
- `SELECT rejection_reason, COUNT(*)` query returns structured reasons, not nulls.
- All existing tests pass; new tests cover each resolution table row.

# Promotion Layer Phase 2: Decision Engine

## Goal

Assign one deterministic decision — `new`, `confirm`, `update`, or `review` —
to each candidate that passed the promotion gate.

## Context

The decision engine answers: given this candidate and the current state of
`brain/wiki/`, what should happen? The answer is derived from existing wiki note
lookup, candidate type and title matching, confidence level, lifecycle state, and
the `duplicate_state` label from the west flange.

The existing `bridge.decide_upsert()` function in `src/second_brain/bridge.py`
already implements note lookup and a rudimentary decision vocabulary. Phase 2
reuses that function and wraps it with the promotion-layer guards (low-confidence
routing, conflict detection, lifecycle checks) that belong here and not in bridge.

The west flange intentionally passes near-duplicate sources through projection
rather than suppressing them (only exact byte duplicates are skipped). This means
a source marked `canonical_duplicate` or `lsh_duplicate` already cleared the
west-flange filter and was judged worth extracting again. The decision engine
treats that label as a prior: this candidate is more likely a corroboration than
new content, and if a matching wiki note exists it is almost certainly a `confirm`
rather than an `update`.

## Scope

New module: `src/second_brain/promotion/decisions.py`

New dataclass `PromotionDecision` in `src/second_brain/promotion/models.py`:
- `decision: str` — one of `'new'`, `'confirm'`, `'update'`, `'review'`
- `target_note_id: str | None` — existing note ID if `confirm` or `update`
- `reason: str` — human-readable explanation
- `conflict_note_ids: list[str]` — IDs of notes that conflict, if any

## Decision Rules

Rules are evaluated in order. The first matching rule wins.

The `decide()` function receives `duplicate_state` from the extraction queue row
alongside the candidate payload. This value is `None` for novel sources,
`"exact_duplicate"` (should not reach promotion), `"canonical_duplicate"`, or
`"lsh_duplicate"` for near-duplicate sources.

### Rule 0 — Near-duplicate reinforcement prior

If `duplicate_state` is `"canonical_duplicate"` or `"lsh_duplicate"` and a
matching wiki note exists, bias toward `confirm` unless the candidate contains
genuinely new components (body diff exceeds threshold). The prior does not
override a conflict or type collision — those still route to `review`.

### Rule 1 — Low-confidence guard

If `candidate["confidence"] == "low"`, decision is `review` with reason
`low_confidence`. This rule fires before any wiki lookup.

### Rule 2 — Policy: auto-review on conflict

If `config.promotion.auto_review_on_conflict` is true (default), and `decide_upsert()`
returns a `CandidateMatch` whose `decision` is `"conflict"`, the promotion layer
maps that to `review` with reason `wiki_conflict`.

### Rule 3 — Title slug collision across types

If a note exists with the same title slug but a different type, decision is
`review` with reason `type_collision`.

### Rule 4 — No existing note

If `decide_upsert()` finds no match (decision `"create"`), promotion decision
is `new`.

### Rule 5 — Existing note matches; candidate does not refine

If `decide_upsert()` returns `"confirm"`, promotion decision is `confirm`.
The candidate has no new content; only confidence/usefulness signals are
updated.

### Rule 6 — Existing note found; candidate refines

If `decide_upsert()` returns `"update"`, promotion decision is `update`.
Body, confidence, steps, source refs, or other durable fields will be merged.

### Rule 7 — Contested lifecycle state

If the existing note has `lifecycle_state` of `"contested"`, decision is
`review` regardless of other matches.

## Decision Interface

```python
def decide(
    candidate: dict,
    *,
    duplicate_state: str | None,
    conn: sqlite3.Connection,
    config: AppConfig,
) -> PromotionDecision:
    ...
```

Takes the deserialized candidate payload, the `duplicate_state` from the
extraction queue row, a database connection for wiki lookups, and the app config.
Returns a `PromotionDecision`. Does not write to the database or filesystem.

## Milestones

1. `decisions.py` module exists with `decide()` function.
2. `PromotionDecision` dataclass exists in `promotion/models.py`.
3. All 8 rules (0–7) are exercised by tests.
4. `decide()` calls `bridge.decide_upsert()` for wiki lookup; it does not
   duplicate note-matching logic.
5. `decide()` accepts and uses `duplicate_state`.
6. `decide()` does not write to wiki or the database.

## Verification

- Candidate with `duplicate_state="canonical_duplicate"` and matching wiki note
  returns `confirm` (rule 0 prior), not `update`.
- Candidate with `duplicate_state="canonical_duplicate"` but conflicting note
  returns `review` (conflict overrides prior).
- Candidate with `confidence="low"` returns `review` (rule 1) before any
  wiki lookup is attempted.
- Candidate with no matching wiki note returns `new`.
- Candidate matching an existing note with no new content returns `confirm`.
- Candidate matching an existing note with differing body/steps returns `update`.
- Candidate whose title slug exists under a different type returns `review`
  with `type_collision`.
- Candidate matching an existing `contested` note returns `review`.
- `brain/wiki/` is unchanged after running the decision engine.
- Database is unchanged after running the decision engine.

## Out Of Scope

- Wiki writes (Phase 4).
- Note rendering (Phase 3).
- Ledger writes (Phase 4).
- Changing the note-matching logic inside `bridge.decide_upsert()`.

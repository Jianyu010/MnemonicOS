# Retrieval Assessment Phase 8: Relevance-Gate-Aware Assessment and Suppression Classification

## Goal

Make Stage 6 aware of the new relevance gate (Phase 6 of Stage 4). When all
candidates are suppressed by the gate, Stage 6 must classify this correctly as
`insufficient_no_hit` with a distinct reason code — not as a corpus miss.
Stage 6 also gains access to gate suppression counts for richer directive
metadata.

## Scope

- New reason code: `relevance_gate_suppressed_all`
- `insufficient_no_hit` state is now reachable via two distinct paths:
  1. Legacy: no candidates recalled at all (corpus miss or empty wiki)
  2. New: candidates recalled but all suppressed by relevance gate
- `partial` state gains a sub-case: some candidates suppressed, some passed
- Gate suppression count surfaces in `AssessmentDirective` for observability
- No change to the state precedence table (gate suppression → `insufficient_no_hit`
  still has lowest priority after contested/stale, consistent with existing rules)

## Components

```text
src/second_brain/assessor.py     — gate-suppressed reason codes
src/second_brain/models.py       — AssessmentDirective: new suppression fields
src/second_brain/runtime.py      — pass suppression metadata from Stage 4
tests/test_assessor.py           — gate-suppressed fixture coverage
```

## Updated `insufficient_no_hit` Classification

Stage 4 must pass suppression metadata to Stage 5 assembly (via `assembly_notes`)
and ultimately to Stage 6. The assessor reads:

```
assembly_notes.suppressed_by_gate_count:  int    # candidates suppressed by relevance gate
assembly_notes.passed_gate_count:         int    # candidates that passed the gate
```

Updated state rule for `insufficient_no_hit`:

| Condition | Reason Code | Note |
|-----------|-------------|------|
| `primary_memories` empty AND `archive_context` empty AND `suppressed_by_gate_count == 0` | `no_primary_or_archive` | True corpus miss or empty wiki |
| `primary_memories` empty AND `archive_context` empty AND `suppressed_by_gate_count > 0` | `relevance_gate_suppressed_all` | Recalled candidates, all below relevance threshold |

Both produce `insufficient_no_hit` state, but the reason code distinguishes
them for diagnostics, logging, and potential future gate calibration.

The recommendation and verification action for both are the same:
- `recommendation: research_fresh`
- `verification_action: do_fresh_research`
- `reuse_mode: fresh_research_required`

## New `partial` Sub-Case

When some candidates pass the gate and some are suppressed:

```
passed_gate_count > 0 AND suppressed_by_gate_count > 0
```

This does not change the state (partial is already expressed when quality-failed
notes are present), but the reason list gains `some_candidates_gate_suppressed`
to surface that retrieval had a broader recall pool that was partially filtered.

This helps future diagnostics distinguish "naturally partial recall" from "strong
recall with aggressive gate filtering." If suppression rate exceeds 80% of the
pool, Stage 6 should also set `followup_query_policy = on_first_failure`
regardless of completion_state, since the gate may have cut useful context.

## AssessmentDirective Extensions

```python
@dataclass
class AssessmentDirective:
    ...
    suppressed_by_gate_count: int = 0
    passed_gate_count: int = 0
    suppression_rate: float = 0.0    # suppressed / (suppressed + passed)
```

These fields are instrumentation and advisory — they do not alter the state
machine. Stage 7 should surface them in the JSON output under
`assessment_metadata` and in the Markdown output as a brief note when
`suppression_rate > 0.5`.

## Rules

1. Stage 4 logs suppressed candidates in `retrieval_hits` with `selected = 0`
   and `suppression_reason = 'relevance_gate'`. Stage 6 does not re-read the DB;
   it reads suppression counts forwarded through `assembly_notes`.
2. Gate-suppressed `insufficient_no_hit` must never produce a `reuse` or
   `reuse_with_verification` recommendation. The gate suppressed because no hit
   was relevant; fresh research is always required.
3. Exact-match hits bypass the gate (Phase 6 rule) and thus can never contribute
   to `suppressed_by_gate_count`.
4. Archive hits are not subject to the wiki relevance gate. Archive fallback may
   still produce `archive_context` even when all wiki candidates are suppressed;
   in that case the state is `partial` (archive-only), not `insufficient_no_hit`.
5. The `suppression_rate` field is informational. It does not alter the state
   table. Future training may use it to detect gate threshold drift.

## Verification

- Query with no relevant notes: all candidates suppressed → `insufficient_no_hit`
  with reason `relevance_gate_suppressed_all`
- Query with relevant notes: candidates pass gate → normal state table applies
- Mixed pool (some suppressed, some passed): `partial` reason includes
  `some_candidates_gate_suppressed`, `suppression_rate` in directive
- `suppressed_by_gate_count` and `passed_gate_count` are correct for fixtures
  with known gate inputs
- Archive fallback with all wiki notes suppressed → `partial` (archive-only),
  NOT `insufficient_no_hit`

---

## Reference

**Governing plan:** [retrieval-assessment-plan.md](retrieval-assessment-plan.md)
**Prior phases:** [P1](retrieval-assessment-plan-phase1.md) · [P2](retrieval-assessment-plan-phase2.md) · [P3](retrieval-assessment-plan-phase3.md) · [P4](retrieval-assessment-plan-phase4.md) · [P5](retrieval-assessment-plan-phase5.md) · [P6](retrieval-assessment-plan-phase6.md) · [P7](retrieval-assessment-plan-phase7.md)
**Stage 4 gate:** [retrieval-reranking-plan-phase6.md](retrieval-reranking-plan-phase6.md)
**Emission:** [retrieval-emission-plan-phase8.md](retrieval-emission-plan-phase8.md)

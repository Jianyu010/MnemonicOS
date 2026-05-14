# Retrieval Assessment Phase 7: Metrics, Degradation, and Stage 7 Handoff

## Goal

Make Stage 6 observable and resilient. Assessment should produce useful metrics
and a complete Stage 7 handoff even when the packet is empty, malformed,
overbudget, or partially degraded.

## Scope

- assessment metrics
- directive reason codes
- graceful degradation
- Stage 7 handoff requirements
- tests for malformed and edge packets
- no-raise policy

## Components

```text
src/second_brain/assessor.py     — degradation and metric payload helpers
src/second_brain/runtime.py      — runtime metrics integration
src/second_brain/models.py       — directive / metric fields
tests/test_runtime_protocol.py   — degraded-packet fixtures
```

## Metrics

Suggested assessment metric fields:

```text
retrieval_state
completion_state
reuse_mode
emission_profile
hydration_policy
selected_branch_count
deferred_branch_count
missing_role_count
warning_count
contradiction_count
fresh_pipeline_threshold
trace_completion_status
assessment_degraded: bool
assessment_reason_codes: list[str]
```

Metrics are instrumentation only. They must not write canonical wiki state.

## Degradation Rules

1. Empty packet returns `insufficient_no_hit`, `fragmentary`, `no_memory`,
   `summaries_only`, and `fresh_research_required`.
2. Malformed reservoir branches are ignored with `assessment_degraded = true`.
3. Missing assembly notes are recomputed only from packet fields available in
   memory; Stage 6 must not read the database.
4. Contradiction parsing failure degrades to `partial` or
   `insufficient_contested` if safe branch selection is impossible.
5. Exceptions are contained and converted into a minimal directive.

## Stage 7 Handoff

Stage 7 needs:

- selected branch IDs
- deferred branch IDs and reasons
- emission profile
- hydration policy
- warning/contradiction reason codes
- missing roles
- fresh pipeline threshold and follow-up policy

Stage 7 must not reclassify trust or completeness. It can only format and
hydrate according to the directive.

## Verification

- empty packet emits minimal no-memory directive
- malformed reservoir branch does not raise
- missing assembly notes still produce a conservative directive
- metrics expose selected/deferred branch counts
- Stage 7 fixture can render from directive fields alone

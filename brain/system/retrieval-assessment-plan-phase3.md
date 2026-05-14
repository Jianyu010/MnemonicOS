# Retrieval Assessment Phase 3: Functional Completeness Classifier

## Goal

Classify whether the assembled packet is complete enough for the caller's
immediate purpose. This phase is separate from trustworthiness: a packet can be
safe but fragmentary, unsafe but complete, or complete but overabundant.

## Scope

- `completion_state`
- missing-role interpretation
- caller-intent role requirements
- trace-completion result interpretation
- fragmentary vs partial-chain distinction
- success-only and overabundant detection

## Components

```text
src/second_brain/assessor.py     — eventual completeness classifier
src/second_brain/models.py       — CompletionState enum / directive fields
tests/test_runtime_protocol.py   — completeness fixtures
```

## Inputs

- `MemoryPacket.functional_path`
- `MemoryPacket.assessment_reservoir`
- `assembly_notes.functional_path_present`
- `assembly_notes.functional_path_confidence`
- `assembly_notes.functional_path_terminal_state`
- `assembly_notes.missing_functional_roles`
- `assembly_notes.trace_completion_status`
- `assembly_notes.assessment_reservoir_branch_count`
- query type, temporality, task kind, and caller intent

## Completion States

| State | Condition |
|-------|-----------|
| `complete_path` | Required roles for the caller intent are present and path confidence is not `none`. |
| `success_only` | Success/current procedure exists but context, failure, diagnosis, or caution roles are thin. |
| `partial_chain` | At least two linked path items exist, but one or more required roles are missing. |
| `fragmentary` | Isolated relevant hits exist without actionable path structure. |
| `overabundant` | Reservoir contains multiple useful branches or full history beyond first-response needs. |

## Caller Intent Role Rules

```text
implementation/procedure:
  required: current_reusable_procedure or successful_resolution
  helpful: decision_pivot, diagnosis, caution

debugging/repair:
  required: failed_attempt or diagnosis, plus successful_resolution/current_reusable_procedure when available
  helpful: caution, decision_pivot

historical:
  required: timeline or terminal state
  helpful: supersession, before/after, decision_pivot

comparison/choice:
  required: contrast or decision_pivot
  helpful: supersession, caution, current path

factual:
  required: relevant primary memory
  path roles usually not required
```

## Trace Status Interpretation

- `completed`: use missing-role fields and reservoir branch count normally.
- `skipped`: if path remains thin, classify as `fragmentary` or `success_only`.
- `not_needed`: flat/factual packets can still be complete without a path.
- `budget_limited`: prefer `partial_chain` or `overabundant` with assessment
  reason `trace_budget_limited`.
- `conflicted`: escalate to trustworthiness/contradiction handling.

## Verification

- complete implementation path classifies as `complete_path`
- success without prerequisites/cautions classifies as `success_only`
- trace-completed but role-missing packet classifies as `partial_chain`
- isolated relevant memory classifies as `fragmentary` for procedural/debug
  intent but can be complete for factual intent
- multi-branch reservoir classifies as `overabundant`

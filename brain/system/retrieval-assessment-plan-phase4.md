# Retrieval Assessment Phase 4: Progressive Disclosure and Reservoir Selection

## Goal

Choose which assessment-reservoir branches Stage 7 should emit now and which
branches should remain deferred. The aim is to provide useful guidance without
flooding the caller with the whole linked history.

## Scope

- `selected_branch_ids`
- `deferred_branch_ids`
- `emission_profile`
- `hydration_policy`
- `followup_query_policy`
- progressive disclosure rules for build/debug/historical/comparison tasks

## Components

```text
src/second_brain/assessor.py     — eventual branch selector
src/second_brain/models.py       — directive fields
tests/test_runtime_protocol.py   — reservoir-selection fixtures
```

## Inputs

- `assessment_reservoir`
- `assembly_notes.default_branch_id`
- `assembly_notes.deferred_branch_ids`
- `assembly_notes.deferred_branch_reasons`
- `completion_state`
- `retrieval_state`
- task kind / query type / temporality
- warning and contradiction IDs

## Selection Rules

Default branch selection:

1. use Stage 5 `default_branch_id` when present and not contradiction-blocked
2. prefer a current success/procedure branch for implementation/debug tasks
3. prefer timeline/supersession branch for historical tasks
4. prefer contrast/decision branch for comparison tasks
5. select no branch for no-hit or warning-only packets

Deferred branches remain available but hidden from first emission unless the
caller explicitly asks for full history or the branch contains a major warning
needed to avoid harm.

## Emission Profiles

| Profile | Use when |
|---------|----------|
| `concise_success_path` | One current reusable path is enough for first action. |
| `success_plus_major_failures` | Failure/caution context materially changes the action. |
| `full_chain` | Caller asks for history or the chain itself is the answer. |
| `warning_only` | Memory exists but should not be reused directly. |
| `no_memory` | No useful memory or archive context exists. |

## Hydration Policies

| Policy | Meaning |
|--------|---------|
| `hydrate_primary_only` | Stage 7 hydrates only primary/default success branch. |
| `hydrate_primary_and_failure_branches` | Stage 7 hydrates default branch plus major failure/caution branches. |
| `hydrate_full_functional_path` | Stage 7 hydrates all selected path branches. |
| `summaries_only` | Stage 7 emits summaries and metadata only. |

## Follow-Up Policy

Prefer `on_first_failure` when deferred branches exist but the caller has not
yet supplied a concrete error signature. Use `immediate_focused_requery` only
when Stage 6 fresh-pipeline thresholds say the reservoir cannot answer the
current purpose.

## Verification

- overabundant reservoir selects one default branch and defers others
- debugging packet emits success plus major failures when needed
- historical query can select `full_chain`
- warning-only packet does not hydrate full bodies unnecessarily
- deferred branch reasons survive into the directive

# Retrieval Emission Phase 5: Directive-Guided Branch Filtering

## Goal

Apply Stage 6's selected/deferred branch decision faithfully. Stage 7 should
emit only the branch set selected by `AssessmentDirective`, with compact
deferred context when the directive says follow-up may be needed later. If Stage
6 requests a focused requery, Stage 7 should emit the query handoff while still
avoiding any internal recursive retrieval.

## Scope

- `selected_branch_ids`
- `deferred_branch_ids`
- `assessment_reservoir` rendering view
- explanatory order vs rank order
- overabundant packet behavior
- focused requery instruction generation
- stable anchor propagation into the suggested query
- retry budget and no-new-result cutoff metadata

## Filtering Algorithm

```text
if emission_profile is no_memory:
  emit no-memory view
elif emission_profile is warning_only:
  emit caution view
else:
  include packet items whose branch_id is selected or branch_id is empty primary
  preserve assembly_order / functional_path order
  retain rank_order as provenance metadata
  summarize deferred_branch_ids without rendering full branch content
if followup_query_policy is immediate_focused_requery:
  attach focused followup_query instruction with stable anchors
elif followup_query_policy is on_first_failure:
  attach conditional followup_query instruction with stable anchors
attach retry_control metadata for external orchestrators
```

## Anchor Extraction

Stage 7 should not discover new anchors, but it should preserve anchors already
present in the packet/directive:

- original task intent
- missing roles
- selected and relevant deferred note IDs
- `chain_id`
- `branch_id`
- error signature or failure text
- relation target ID from functional path, contradiction, warning, or reservoir
  metadata

The suggested query string should be compact but anchored. Prefer:

```text
auth rollout recovery success_resolution chain_id:trace/auth-rollout-recovery procedure/recover-auth-rollout
```

over:

```text
find more about auth rollout
```

## Retry Cutoff

Stage 7 may compare current retry metadata supplied by the orchestrator with the
current packet IDs and directive state. It should not execute the retry, but it
can declare that no further automatic retry is allowed.

Cutoff reasons:

| Reason | Meaning |
|--------|---------|
| `retry_budget_exhausted` | `retry_attempt >= max_focused_retries` |
| `no_new_ids` | focused pass returned no stable IDs not seen before |
| `same_suggested_query` | generated query repeats a prior retry query |
| `same_threshold_and_roles` | Stage 6 returned the same threshold and missing roles |
| `terminal_insufficient_state` | focused pass ended in no-hit, stale, or contested state |

Cutoff output should preserve the best available packet and say no further
automatic focused query should run.

If no branch IDs are present, fallback to selected packet slots in Stage 5
assembly order and mark `emission_degraded = true` in metrics.

## Ordering

Stage 7 must preserve Stage 5 explanatory order. It may display score/rank as
metadata, but it must not reorder selected branches by Stage 4 score.

## Verification

- selected branch renders
- deferred branch is summarized but not fully emitted
- full-chain profile emits all directive-selected branches
- immediate focused requery emits a suggested query and starts no nested pass
- on-first-failure emits a conditional suggested query
- suggested query includes stable anchors when available
- no-new-IDs retry emits cutoff instead of another suggested retry
- rank order preserved as metadata
- missing branch IDs degrade without raising

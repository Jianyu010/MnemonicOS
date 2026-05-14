# Retrieval Emission Phase 6: Metrics and Retrieval Trace Emission

## Goal

Record enough emission and directive-specific metrics to audit retrieval quality
without writing canonical wiki state or mixing instrumentation into retrieval
logic.

## Scope

- runtime packet metrics expansion
- emission metrics
- retrieval packet trace event payloads
- separation from canonical writes
- no duplicate assessment metrics

## Suggested Metric Fields

```text
retrieval_state
completion_state
reuse_mode
recommendation
verification_action
emission_profile
hydration_policy
selected_branch_count
deferred_branch_count
warning_count
contradiction_count
missing_role_count
fresh_pipeline_threshold
followup_query_policy
followup_query_emitted
auto_retry_allowed
retry_attempt
max_focused_retries
retry_cutoff_reason
retrieval_pass_count
final_pass_index
retry_new_id_count
archive_context_count
emission_degraded
```

Metrics may be stored in `runtime_packet_metrics` or a future emission-specific
instrumentation table. The choice should be made during implementation, but the
schema should keep directive fields queryable.

## Trace Event

Bridge/orchestrator paths that emit `retrieval_packet` events should include the
directive summary and selected memory IDs. They should not store full Markdown
as the only representation of what was emitted.

## Verification

- metrics include directive fields
- no canonical wiki tables are mutated by emission
- repeated rendering does not duplicate canonical state
- retrieval packet event includes selected IDs and state/recommendation
- retrieval packet event includes follow-up query policy and suggested query
  metadata when emitted
- retry trace includes attempt count, previous query IDs when available, new ID
  count, and cutoff reason
- composed multi-pass response metrics include pass count and final pass index
- direct JSON path and orchestrator path produce comparable metric payloads

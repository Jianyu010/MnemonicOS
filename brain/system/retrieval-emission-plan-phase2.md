# Retrieval Emission Phase 2: JSON Renderer Parity

## Goal

Make JSON the canonical Stage 7 output format and align direct retrieval,
runtime, shell, and HTTP JSON paths around one schema.

## Scope

- `retrieve()` / `result_to_json()` compatibility gap
- `resolve_task_context_to_json()` parity
- `archive_context` and `assessment_directive` exposure
- stable field names for downstream tools
- schema regression tests
- opt-in direct-retrieve inspection hydration fields

## Current Gap

The direct `RetrieveResult` JSON path currently exposes hits and archive hits,
but not the Stage 6 directive fields. Runtime context JSON exposes
`assessment_directive` and `memory_packet`, but Stage 7 does not yet normalize
the presentation across paths.

The direct retrieval JSON path also has a deliberate inspection-only extension:
callers may request body hydration for selected wiki hits. This is additive and
off by default.

## Target JSON Sections

```text
{
  "protocol": "...",
  "version": "...",
  "operation": "...",
  "retrieval_state": "...",
  "recommendation": "...",
  "verification_action": "...",
  "assessment_directive": {...},
  "memory": {... selected packet view ...},
  "archive_context": [...],
  "warnings": [...],
  "contradictions": [...],
  "deferred_context": {...},
  "followup_query": {
    "policy": "...",
    "threshold": "...",
    "missing_roles": [...],
    "anchors": {
      "original_intent": "...",
      "note_ids": [...],
      "chain_id": "...",
      "branch_id": "...",
      "error_signature": "...",
      "relation_target": "..."
    },
    "suggested_query": "..."
  },
  "retry_control": {
    "auto_retry_allowed": false,
    "max_focused_retries": 1,
    "retry_attempt": 0,
    "stop_if_no_new_ids": true,
    "stop_if_same_threshold": true,
    "stop_if_same_missing_roles": true,
    "cutoff_reason": null
  },
  "retrieval_passes": [
    {
      "pass_index": 1,
      "query_id": 123,
      "query_text": "...",
      "emission": {...},
      "triggered_followup_query": {...}
    }
  ],
  "final_pass_index": 1,
  "auto_retry_summary": {
    "attempted": false,
    "attempt_count": 0,
    "new_ids": [],
    "cutoff_reason": null
  },
  "provenance": {...},
  "debug": {...}
}
```

## Compatibility Rule

Legacy `hits` and `archive_hits` may remain for direct retrieval compatibility,
but the Stage 7 schema should make the assessed packet shape the preferred
consumer surface.

For direct retrieve compatibility, `hits[]` remains compact by default. When a
caller explicitly opts in with CLI `--include-body` or HTTP `include_body`, wiki
hits may include:

```json
{
  "body": "... hydrated wiki note body ...",
  "body_truncated": true
}
```

`body_chars` caps each hydrated body. Archive chunks are not hydrated by this
inspection path; their normal `summary`, `excerpt`, and `body_path` fields remain
the supported surface.

## Verification

- JSON snapshots for direct retrieval and runtime context
- HTTP parity against Python API
- shell `--json` parity against Python API
- direct retrieve JSON omits `body` by default and includes `body` /
  `body_truncated` only when body hydration is requested
- archive-only JSON includes `archive_context`, not only `archive_hits`
- directive fields appear at top level and in `assessment_directive`
- immediate focused requery JSON includes `followup_query` and does not trigger
  a nested retrieval result
- follow-up query JSON preserves stable anchors for pass-2 retrieval
- retry control JSON exposes attempt count, retry budget, and cutoff reason
- automatic retry JSON returns both pass emissions and marks `final_pass_index`

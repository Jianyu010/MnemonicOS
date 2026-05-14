# Retrieval Emission Phase 7: Shell/API Consolidation and No-Raise Degradation

## Goal

Route all Stage 7 output surfaces through the same render model and guarantee
that emission never crashes the retrieval call. Bad packets should produce
conservative degraded output.

## Scope

- shell command output
- HTTP output
- Python API output
- direct retrieval output
- degraded-packet handling
- no hidden recursive retrieval
- external automatic retry policy
- hard cutoff when focused retry discovers no new entries
- composed multi-pass response shape
- final end-to-end tests

## Consolidation Targets

```text
mnemonic_context.sh / CLI --json
POST /v1/retrieve
resolve_task_context
direct retrieve() JSON compatibility
orchestrator context_markdown
```

All paths should use the same JSON render model and, where Markdown is needed,
the same Markdown renderer.

## Degradation Rules

- Empty packet renders `no_memory`.
- Missing directive renders conservative warning-only output and marks degraded.
- Malformed directive falls back to compatibility fields if available.
- Malformed reservoir branch is ignored with a warning metric.
- Renderer exceptions are caught and converted to minimal JSON/Markdown.
- `immediate_focused_requery` produces an outbound instruction, not an internal
  call back into Stage 1.
- Automatic retry may be performed by an external orchestrator only when
  `retry_control.auto_retry_allowed = true`.
- Automatic retry stops when retry budget is exhausted, no new stable IDs are
  returned, the same threshold/missing roles repeat, or the focused query is a
  duplicate.
- When automatic retry runs, both Stage 7 outputs are returned in a composed
  response; the focused pass may be final, but the original pass remains as
  trace/provenance.

## Verification

- Python, shell, and HTTP parity tests
- malformed packet fixture does not raise
- missing directive fixture does not raise
- no-memory Markdown and JSON are both explicit
- immediate focused requery output contains a focused suggested query and no
  nested retrieval result
- automatic retry enabled fixture starts pass 2 outside Stage 7
- no-new-entry retry fixture emits cutoff and no further suggested retry
- automatic retry response contains pass 1 and pass 2 emissions plus
  `final_pass_index`
- final retrieval-path smoke test covers Stage 1 through Stage 7 emission

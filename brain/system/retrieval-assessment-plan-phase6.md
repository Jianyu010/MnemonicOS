# Retrieval Assessment Phase 6: Runtime and Shell Path Consolidation

## Goal

Ensure all retrieval entry points use the same Stage 6 classifier. Runtime,
shell, API, and payload assessment should not drift into separate sufficiency
rules.

## Scope

- shared assessor module boundary
- migration from `runtime.assess_retrieval()` compatibility logic
- `assess_retrieval_payload()` compatibility
- `resolve_task_context()` packet-level assessment
- shell/API JSON behavior
- no duplicate state classifier logic

## Components

```text
src/second_brain/assessor.py     — target shared Stage 6 implementation
src/second_brain/runtime.py      — compatibility wrappers
src/second_brain/bridge.py       — shell/API bridge callers
src/second_brain/cli.py          — command output path
tests/test_runtime_protocol.py   — runtime fixtures
tests/test_orchestrator.py       — HTTP/API parity fixtures
```

## Consolidation Rules

1. `assessor.py` owns the canonical state and directive classifiers.
2. Existing public functions may remain as wrappers for compatibility.
3. Runtime and shell payload paths must produce the same state for equivalent
   inputs.
4. Packet-level assessment is preferred when `MemoryPacket` is available.
5. Hit-only assessment remains supported for legacy `retrieve` callers but
   should be internally mapped onto the shared classifier when possible.

## Migration Strategy

1. Add `assessor.py` with pure functions and no DB access.
2. Move existing hit-state logic behind compatibility wrappers.
3. Add packet-level `assess_packet()` consuming `MemoryPacket`.
4. Update `resolve_task_context()` to attach `AssessmentDirective`.
5. Update shell/API serialization after Stage 7 plan is ready.

## Verification

- runtime and payload assessment agree for equivalent current/stale/contested
  fixtures
- packet-level assessment adds completion and directive fields
- legacy callers still receive `retrieval_state`, `recommendation`, and
  `verification_action`
- no Stage 6 function opens a DB connection
- no duplicated classifier branches remain in runtime/bridge once migration is
  complete

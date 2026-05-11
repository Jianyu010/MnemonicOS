# LLM/VLM Layer Phase 7: Daemon and Admin Surfaces

## Goal

Expose extraction as ingestion/admin infrastructure while keeping normal
north-flange query runtime decoupled.

## Scope

This phase wires queueing and processing into daemon, CLI, and HTTP surfaces.
These surfaces are explicit ingestion controls, not task-context retrieval
dependencies.

## Public Surfaces

CLI:

- queue extraction for artifact source
- queue extraction for episode/session source
- inspect extraction status
- run extraction queue job, with dry-run mode

HTTP/API:

- queue extraction
- process extraction queue
- inspect extraction status

Daemon:

- scheduled `process_extraction_queue`
- status output for extraction queue health

## North/East Boundary

Normal query flow remains:

```text
north caller
  -> east retrieval packet
  -> agent/human uses memory
```

Ingestion/admin flow may be:

```text
north caller
  -> queue raw source for extraction
  -> west projection
  -> LLM/VLM extraction
  -> promotion-ready candidates
```

The extraction daemon must not be called by `resolve_task_context`.

## Milestones

1. CLI can queue artifact extraction.
2. CLI can queue episode extraction.
3. CLI can inspect extraction status.
4. HTTP API mirrors CLI queue/status behavior.
5. Daemon job drains pending queue rows.
6. Daemon status reports extraction queue health.
7. Retrieval/runtime tests prove the query path does not invoke extraction.

## Verification

- `process_extraction_queue --dry-run` reports pending work.
- Daemon schedule includes extraction queue job.
- API can queue and inspect extraction work.
- API processing endpoint can run dry-run safely.
- `resolve_task_context` does not call extraction worker.
- Retrieval still reads canonical notes and indexes only.
- No admin surface writes wiki as part of the LLM/VLM layer.

## Out Of Scope

- Deterministic wiki promotion implementation.
- User-facing review UI.
- RL or reward optimization.


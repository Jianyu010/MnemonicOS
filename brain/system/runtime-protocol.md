# MnemonicOS Runtime Protocol v0.1

## Purpose

This document defines the runtime contract for MnemonicOS as a model-agnostic
memory operating layer.

The protocol is:

- runtime-first and transport-agnostic
- compatible with the current CLI, scripts, and daemon control plane
- designed for a local callable engine with a Unix-socket daemon as the
  canonical v1 service runtime

This protocol does not replace `retrieve`, maintenance, or intake flows. It
defines the canonical runtime contract above them. Bridge scripts and
agent-specific integrations are compatibility adapters, not the architectural
center.

> **Note on intake:** Legacy references to `ingest-session` as the fast-path
> intake describe a pre-west-flange path. Current intake goes through the
> west-flange artifact and episode capture model (see `ingestion-west-flange-plan.md`
> and `ingestion-capture-plan.md`). The protocol semantics below remain
> correct; only the underlying intake substrate has changed.

## Design Goals

- make behavior consistent across any caller that uses MnemonicOS
- standardize task and session tracking
- return policy-aware memory guidance rather than raw ranked hits alone
- keep deterministic control over state transitions and recommendations
- separate the fast path from slow-path maintenance and organization work

## Versioning

- protocol name: `mnemos-runtime`
- protocol version: `0.1`
- compatibility stance: additive changes preferred; breaking semantic changes
  require a version bump

## Runtime Operations

MnemonicOS exposes four logical operations:

1. `resolve_task_context`
2. `record_outcome`
3. `decide_writeback`
4. `run_maintenance`

These are protocol operations, not transport requirements. They may be exposed
through local Python calls, shell scripts, Unix-socket daemon commands, or HTTP
endpoints.

## Execution Model

Fast path:

- `resolve_task_context`
- `record_outcome`
- intake (west-flange artifact or episode capture)
- lightweight writeback policy decisions

Slow path:

- `embed_backfill`
- `daily_consolidation`
- `weekly_hygiene`
- `status_report`

Rules:

- fast-path intake archives as true raw immediately
- canonical note mutation does not happen in default intake
- extraction queue and VLM/LLM processing are slow-path operations
- raw archive material is retrievable only through archive fallback, not as
  canonical wiki memory

## Shared Concepts

### Caller Identity

- `bridge_id`: stable identity for the calling runtime adapter, shell
  integration, service client, or job runner
- examples: `codex`, `claude`, `local-cli`, `job:daily-consolidation`

Rules:

- `bridge_id` is the canonical runtime field
- deprecated aliases like `agent` should be normalized to `bridge_id`

### Task Identity

`task_id` identifies one logical unit of work from the caller's perspective.

Examples:

- `codex:2026-04-12T22:58:10+08:00:debug-auth-deploy`
- `claude:2026-04-12T23:01:44+08:00:release-checklist`

Rules:

- `task_id` must remain stable across retries for the same user-visible task
- a new user request should usually create a new `task_id`
- the first meaningful failure should not create a new `task_id`

### Session Identity

`session_id` identifies a caller session that may contain one or more tasks.

Examples:

- `codex-2026-04-12-225810`
- `claude-2026-04-12-230144`

Rules:

- `session_id` is broader than `task_id`
- one session may contain multiple `task_id` values
- writeback, usefulness, and retrieval logging should retain both fields

### Task Kind

Allowed values:

- `coding`, `debugging`, `knowledge`, `architecture`, `research`, `general`

Rules:

- `general` is the fallback
- task kind influences retrieval weighting, warning language, and future
  evaluation slices

### Temporality

- `current`: bias toward freshness-sensitive recommendations
- `historical`: stale but useful material may be reused directly
- `mixed`: current truth preferred but history included
- `unknown`: MnemonicOS may infer from the query

### Recommendation

- `reuse`: current and trustworthy enough to use directly
- `reuse_with_verification`: useful scaffold exists; verify before relying
- `research_fresh`: memory is missing, stale, contested, or unsafe

### Verification Action

- `none`: use directly
- `verify_current_truth`: validate the claim against current state
- `check_recent_sources`: do fresh repo/tool/web checking first
- `treat_as_historical_only`: useful for prior context, not current decisions

### Retrieval State

- `sufficient_current`
- `sufficient_historical`
- `partial`
- `insufficient_no_hit`
- `insufficient_stale`
- `insufficient_contested`

## Operation: `resolve_task_context`

Return a bounded, policy-aware context packet before or during meaningful work.

### Request

```yaml
protocol: mnemos-runtime
version: "0.1"
operation: resolve_task_context
bridge_id: codex
session_id: codex-2026-04-12-225810
task_id: codex:2026-04-12T22:58:10+08:00:debug-auth-deploy
task:
  text: debug the auth deploy failure
  kind: debugging
  temporality: current
  token_budget: 1800
  scope:
    workspace: /Users/jianyulong/ai_memory
    repo: ai_memory
    tags: [deploy, auth]
options:
  include_archive_fallback: true
  top_k: 8
```

### Response

```yaml
protocol: mnemos-runtime
version: "0.1"
operation: resolve_task_context
task_id: codex:2026-04-12T22:58:10+08:00:debug-auth-deploy
session_id: codex-2026-04-12-225810
retrieval_state: partial
recommendation: reuse_with_verification
verification_action: verify_current_truth
memory_packet:
  primary_memories:
    - id: procedure/deploy-auth-service
      title: Deploy Auth Service
      type: procedure
      summary: Current deploy checklist for the auth service.
      freshness_state: fresh
      channels: [bm25, exact]
      final_score: 1.14
  same_episode_context:
    - id: incident/auth-deploy-failure
      title: Auth Deploy Failure
      type: incident
      summary: Previous deploy failed until checklist and schema state were reconciled.
      chain_id: trace/auth-rollout-recovery
      channels: [same_episode]
      relation_types: [part_of_chain]
  related_by_topic:
    - id: decision/auth-database
      title: Auth Database Choice
      type: decision
      summary: Auth deploy procedure depends on the current database decision.
      channels: [graph]
      relation_types: [reverse_linked_decision]
      final_score: 0.42
  warnings: []
```

### Required Response Fields

- `task_id`, `session_id`, `retrieval_state`, `recommendation`,
  `verification_action`, `memory_packet`

### Behavior Rules

- non-trivial tasks should call this operation before major LLM work
- after the first meaningful failure, call again before another retry
- archive fallback may inform the packet; archive-only evidence is not current
  canonical truth
- `primary_memories` should not be dominated by same-episode artifacts
- packet size should respect the caller's token budget

## Operation: `record_outcome`

Capture the outcome of a task for usefulness, writeback, and evaluation signals.

### Request

```yaml
protocol: mnemos-runtime
version: "0.1"
operation: record_outcome
bridge_id: codex
session_id: codex-2026-04-12-225810
task_id: codex:2026-04-12T22:58:10+08:00:debug-auth-deploy
query_id: 142
outcome:
  type: task_result
  status: failure
  summary: Initial deploy fix failed because the release checklist was outdated.
  memory_usefulness:
    useful_ids:
      - procedure/deploy-auth-service
    misleading_ids:
      - procedure/legacy-auth-release
```

### Response

```yaml
status: accepted
writeback_hint:
  recommended_operation: decide_writeback
  candidate_note_ids:
    - procedure/deploy-auth-service
    - incident/auth-deploy-failure-2026-04-12
```

### Behavior Rules

- records runtime evidence; does not directly mutate canonical notes
- usefulness feedback should be tied to retrieved note ids when possible
- `task_id` and `session_id` must be preserved so logs stay attributable

## Operation: `decide_writeback`

Turn a structured task summary into an explicit writeback decision before
ingest mutates the durable vault.

### Allowed Decisions

- `skip`
- `confirm`
- `update`
- `new`
- `incident_only`
- `procedure_refinement`

### Behavior Rules

- `confirm` strengthens confidence signals without rewriting core content
- `update` and `procedure_refinement` prefer existing note ids over duplicates
- `new` is reserved for genuinely missing durable coverage
- `incident_only` captures notable failures without canonical promotion
- the shared runtime decisioning path is canonical; adapters should not
  reimplement their own writeback semantics

## Operation: `run_maintenance`

Run explicit slow-path jobs for consolidation, freshness, conflict detection,
and review generation.

```yaml
operation: run_maintenance
bridge_id: job:daily-consolidation
job:
  name: daily_consolidation
  dry_run: false
```

Rules:

- explicitly slow-path; not required in synchronous agent request path
- current maintenance jobs remain the implementation substrate

## Mapping To Current Surfaces

| Legacy surface | Protocol mapping |
|---|---|
| `mnemonic_context.sh` | compatibility adapter for `resolve_task_context` |
| `mnemonic_postflight.sh` | `decide_writeback` + `record_outcome` |
| `POST /v1/retrieve` | retrieval substrate for `resolve_task_context` |
| `POST /v1/jobs/run` | maps to `run_maintenance` |

## Architectural Position

Authority order:

1. runtime protocol semantics
2. core callable implementation (`runtime.py`, `orchestrator.py`)
3. transport adapters (CLI, HTTP, compatibility bridges)

- agent-specific bridge code must not define the contract
- compatibility integrations may lag slightly as long as semantics are preserved

## Fast Path And Slow Path

Fast path (`resolve_task_context`, `record_outcome`, `decide_writeback`):

- synchronous, deterministic, low-latency
- safe for agent request-time use

Slow path (`run_maintenance`, consolidation, dedupe, freshness, review):

- asynchronous or scheduled
- not required to complete an agent task

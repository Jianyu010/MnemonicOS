# MnemonicOS Runtime Protocol v0.1

## Purpose

This document defines the Phase 1 runtime contract for MnemonicOS as a model-agnostic memory operating layer.

The protocol is:

- runtime-first and transport-agnostic
- compatible with the current CLI, scripts, and minimal HTTP API
- designed for a local callable engine first, with service/daemon adapters later
- grounded in the existing Phase 4 retrieval, graph, trust/freshness, and ingest substrate

This protocol does not replace the current `retrieve`, `ingest-session`, or maintenance flows. It defines the canonical runtime contract above them. Existing bridge scripts and agent-specific integrations are compatibility adapters, not the architectural center.

## Design Goals

- make behavior consistent across any caller that uses MnemonicOS
- standardize task and session tracking
- return policy-aware memory guidance rather than raw ranked hits alone
- keep deterministic control over state transitions and recommendations
- separate the fast path from slow-path maintenance and organization work

## Versioning

- protocol name: `mnemos-runtime`
- protocol version: `0.1`
- compatibility stance: additive changes are preferred during Phase 1; breaking semantic changes require a version bump

## Runtime Operations

MnemonicOS exposes four logical operations:

1. `resolve_task_context`
2. `record_outcome`
3. `decide_writeback`
4. `run_maintenance`

These are protocol operations, not transport requirements. They may be exposed through local Python calls, shell scripts, or HTTP endpoints.

## Shared Concepts

### Caller Identity

- `bridge_id`: stable identity for the calling runtime adapter, shell integration, service client, or job runner
- examples:
  - `codex`
  - `claude`
  - `local-cli`
  - `job:daily-consolidation`

Rules:

- `bridge_id` is the canonical runtime field
- deprecated aliases like `agent` should be normalized into `bridge_id`
- transport-specific adapters must not invent alternate identity fields if `bridge_id` is available

### Task Identity

`task_id` identifies one logical unit of work from the caller's perspective.

Examples:

- `codex:2026-04-12T22:58:10+08:00:debug-auth-deploy`
- `claude:2026-04-12T23:01:44+08:00:release-checklist`

Rules:

- `task_id` must remain stable across retries and intermediate retrievals for the same user-visible task
- a new user request should usually create a new `task_id`
- the first meaningful failure should not create a new `task_id`; it should be recorded as part of the same task unless the scope truly changes

Recommended composition:

- `bridge_id`
- task start timestamp
- normalized task slug

### Session Identity

`session_id` identifies a caller session or execution span that may contain one or more tasks.

Examples:

- `codex-2026-04-12-225810`
- `claude-2026-04-12-230144`

Rules:

- `session_id` is broader than `task_id`
- one session may contain multiple `task_id` values
- writeback, usefulness, and retrieval logging should retain both fields whenever available

### Task Kind

Allowed values for Phase 1:

- `coding`
- `debugging`
- `knowledge`
- `architecture`
- `research`
- `general`

Rules:

- `general` is the fallback when a caller cannot classify more precisely
- task kind influences retrieval weighting, warning language, and future evaluation slices

### Temporality

Temporality expresses whether the task needs current truth or historical memory.

Allowed values:

- `current`
- `historical`
- `mixed`
- `unknown`

Rules:

- `current` should bias toward freshness-sensitive recommendations
- `historical` allows stale but still useful material to be reused directly
- `mixed` means current truth is preferred but history may still be included
- if not supplied, MnemonicOS may infer temporality from the query

### Scope

Scope optionally narrows the relevant memory space.

Phase 1 fields:

- `workspace`
- `project`
- `repo`
- `tags`

Rules:

- scope is advisory for retrieval and writeback
- scope should not be required for correctness

### Recommendation

MnemonicOS returns one of:

- `reuse`
- `reuse_with_verification`
- `research_fresh`

Meaning:

- `reuse`: current and trustworthy enough to use directly
- `reuse_with_verification`: useful scaffold exists, but the agent should verify before relying on it
- `research_fresh`: memory is missing, stale, contested, or otherwise unsafe as a primary basis

### Verification Action

MnemonicOS returns one of:

- `none`
- `verify_current_truth`
- `check_recent_sources`
- `treat_as_historical_only`

Meaning:

- `none`: memory can be used directly
- `verify_current_truth`: validate the specific claim or procedure against current state
- `check_recent_sources`: do fresh repo/tool/web checking before relying on the memory
- `treat_as_historical_only`: useful for prior context, not for current-state decisions

### Retrieval State

Phase 1 standard states:

- `sufficient_current`
- `sufficient_historical`
- `partial`
- `insufficient_no_hit`
- `insufficient_stale`
- `insufficient_contested`

These states should map cleanly to the current compatibility adapters and retrieval assessment logic.

## Operation: `resolve_task_context`

### Purpose

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
      body_path: /vault/wiki/procedures/active/deploy-auth-service.md
      freshness_state: fresh
      channels: [bm25, exact]
      relation_types: []
      final_score: 1.14
      provenance: [channels:bm25,exact, freshness:fresh]
  related_by_topic:
    - id: decision/auth-database
      title: Auth Database Choice
      type: decision
      summary: Auth deploy procedure depends on the current database decision.
      body_path: /vault/wiki/decisions/auth-database.md
      freshness_state: fresh
      channels: [graph]
      relation_types: [reverse_linked_decision]
      final_score: 0.42
      provenance: [channels:graph, freshness:fresh, relations:reverse_linked_decision]
  related_by_timeline: []
  related_by_similarity: []
  related_by_contrast: []
  warnings: []
  verification_needed: []
related_memories: []
provenance_summary: []
debug:
  query_type: troubleshooting
  classifier_confidence: 0.71
```

### Required Response Fields

- `task_id`
- `session_id`
- `retrieval_state`
- `recommendation`
- `verification_action`
- `memory_packet`

### Behavior Rules

- non-trivial tasks should call this operation before major LLM work
- after the first meaningful failure, the caller should invoke it again before another retry
- response assembly should use the existing retrieval stack rather than bypassing it
- archive fallback may inform the packet, but archive-only evidence should not be presented as current canonical truth

### Phase 2 Packet Rules

- `memory_packet` is a bounded context product, not a raw ranked hit dump
- `primary_memories` should preferentially include a useful mix of procedures, fact-like notes, and episode-like notes when available
- related memories should be grouped by relation bucket rather than flattened into the primary list
- packet size should respect the caller's token budget
- provenance should explain why an item appears in the packet, including retrieval channels, freshness state, and relation hints when available
- lifecycle state such as `contested` or `archived` should surface explicit caution in warnings and verification requirements
- when a note is expected to appear in native Obsidian graph or link views, its durable representation should also be expressible through Obsidian-compatible links or link-valued properties, even though the MnemonicOS semantic layer remains canonical

## Operation: `record_outcome`

### Purpose

Capture the outcome of a task so MnemonicOS can update usefulness, future writeback decisions, and evaluation signals.

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
  fresh_research_needed: true
  fresh_sources_checked:
    - repo history
    - deployment docs
  memory_usefulness:
    useful_ids:
      - procedure/deploy-auth-service
    misleading_ids:
      - procedure/legacy-auth-release
```

### Response

```yaml
protocol: mnemos-runtime
version: "0.1"
operation: record_outcome
task_id: codex:2026-04-12T22:58:10+08:00:debug-auth-deploy
session_id: codex-2026-04-12-225810
status: accepted
writeback_hint:
  recommended_operation: decide_writeback
  candidate_note_ids:
    - procedure/deploy-auth-service
    - incident/auth-deploy-failure-2026-04-12
```

### Behavior Rules

- this operation records runtime evidence; it does not directly mutate canonical notes
- when `query_id` is available, usefulness feedback should be written back to retrieval logs as well as stored in the outcome record
- usefulness feedback should be tied to retrieved note ids whenever possible
- `task_id` and `session_id` must be preserved so logs stay attributable

## Operation: `decide_writeback`

### Purpose

Turn a structured task summary into an explicit writeback decision before ingest mutates the durable vault.

### Request

```yaml
protocol: mnemos-runtime
version: "0.1"
operation: decide_writeback
bridge_id: codex
session_id: codex-2026-04-12-225810
task_id: codex:2026-04-12T22:58:10+08:00:debug-auth-deploy
note:
  type: procedure
  title: Deploy Auth Service
  summary_text: Found the current deploy steps and identified the outdated checklist item.
context:
  task_kind: debugging
  retrieval_state: partial
  existing_note_candidates:
    - procedure/deploy-auth-service
  update_target_ids:
    - procedure/deploy-auth-service
  fresh_research_needed: true
  fresh_sources_checked:
    - repo history
    - deployment docs
```

### Response

```yaml
protocol: mnemos-runtime
version: "0.1"
operation: decide_writeback
task_id: codex:2026-04-12T22:58:10+08:00:debug-auth-deploy
session_id: codex-2026-04-12-225810
decision: procedure_refinement
targets:
  - procedure/deploy-auth-service
candidates:
  - procedure/deploy-auth-service
reason: Existing procedure coverage is present, but the session adds a durable refinement based on fresher evidence.
```

### Allowed Decisions

- `skip`
- `confirm`
- `update`
- `new`
- `incident_only`
- `procedure_refinement`

### Behavior Rules

- `confirm` should strengthen confidence or usefulness signals without rewriting core content
- `update` and `procedure_refinement` should prefer existing note ids over creating duplicates
- `new` should be reserved for genuinely missing durable coverage
- `incident_only` captures notable failures without promoting them into canonical knowledge automatically
- the shared runtime decisioning path is canonical; CLI/HTTP/legacy bridge adapters should call into it rather than reimplementing their own writeback semantics

## Operation: `run_maintenance`

### Purpose

Run explicit slow-path jobs for consolidation, freshness, conflict detection, and review generation.

### Request

```yaml
protocol: mnemos-runtime
version: "0.1"
operation: run_maintenance
bridge_id: job:daily-consolidation
session_id: job-2026-04-13-daily-consolidation
job:
  name: daily_consolidation
  dry_run: false
```

### Response

```yaml
protocol: mnemos-runtime
version: "0.1"
operation: run_maintenance
session_id: job-2026-04-13-daily-consolidation
job:
  name: daily_consolidation
  status: ok
stats: {}
review_items_created: []
```

### Behavior Rules

- this operation is explicitly slow-path
- it should not be required in the synchronous agent request path
- current maintenance jobs remain the implementation substrate for this operation

## Mapping To Current Surfaces

### Existing Retrieval Script Flow

Current compatibility surfaces:

- `scripts/mnemonic_should_retrieve.sh`
- `scripts/mnemonic_context.sh`

Phase 1 mapping:

- these are legacy-compatible adapters for `resolve_task_context`
- current `retrieval_state` and `recommendation` already align with the Phase 1 contract
- packet fields can be added incrementally without breaking existing consumers

### Existing Postflight Flow

Current compatibility surfaces:

- `scripts/mnemonic_should_writeback.sh`
- `scripts/mnemonic_should_upsert.sh`
- `scripts/mnemonic_postflight.sh`

Phase 1 mapping:

- summary parsing and candidate selection align with `decide_writeback`
- usefulness and task outcome capture align with `record_outcome`
- final ingest remains a downstream mutation path after an explicit writeback decision

### Existing HTTP API

Current endpoints:

- `POST /v1/retrieve`
- `POST /v1/ingest/session`
- `POST /v1/jobs/run`

Phase 1 mapping:

- `POST /v1/retrieve` remains the retrieval substrate for `resolve_task_context`
- `POST /v1/ingest/session` remains the mutation substrate after writeback decisions are made
- `POST /v1/jobs/run` maps naturally to `run_maintenance`
- the richer runtime contract may later be exposed through new endpoints, but Phase 1 should not require transport proliferation

## Architectural Position

The canonical order of authority is:

1. runtime protocol semantics
2. core callable implementation
3. transport adapters such as CLI, HTTP, and compatibility bridges

Implications:

- Codex-, Claude-, or other agent-specific bridge code must not define the contract
- compatibility integrations may lag slightly behind the canonical runtime as long as semantics stay preserved
- Phase 1 implementation work should prefer shared runtime models and callable entrypoints over adding more bridge-specific branching

## Fast Path And Slow Path

### Fast Path

- `resolve_task_context`
- `record_outcome`
- `decide_writeback`

Properties:

- synchronous
- deterministic by default
- low-latency
- safe for agent request-time use

### Slow Path

- `run_maintenance`
- consolidation
- dedupe
- link refinement
- freshness recomputation
- contradiction handling
- review generation

Properties:

- asynchronous or explicitly scheduled
- not required to complete an agent task
- can consume runtime logs and structured outcomes later

## Phase 1 Non-Goals

- replacing the current retrieval engine
- adding new durable note kinds
- requiring a permanent daemon
- making the HTTP API the canonical source of protocol semantics
- letting small-LLM helpers authoritatively mutate state

## Phase 1 Acceptance Criteria

- multiple independent callers can express the same `bridge_id`, `task_id`, and `session_id` semantics
- current retrieval assessment states map directly into the protocol without semantic drift
- runtime responses can carry `recommendation` and `verification_action` consistently
- writeback decisioning is explicitly represented as a protocol operation, even if the underlying mutation path remains unchanged
- the protocol layers onto current scripts and API without making any one compatibility bridge the source of truth

## Next Implementation Step

After this spec is accepted, the next concrete change should be to implement `resolve_task_context` as the first vertical slice:

- define the runtime request/response models in code
- expose a shared callable path that compatibility adapters can invoke
- add `verification_action`
- keep the current retrieval engine and transport surfaces as the implementation substrate

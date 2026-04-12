## Plan: MnemonicOS Improvement Roadmap vNext

TL;DR: Formalize MnemonicOS as a model-agnostic memory operating system built on the repo's existing Phase 4 foundation: local markdown as source of truth, hybrid retrieval, graph cache, trust/freshness state, maintenance jobs, and agent bridge scripts. The next phase should not rebuild retrieval from scratch. It should harden MnemonicOS into a reusable runtime layer by defining a stable agent protocol, canonical schema overlays and lifecycle states, typed weighted linking between memories, disciplined writeback and consolidation rules, and evaluation loops tied directly to token savings and task success.

### Summary

MnemonicOS is already beyond the "searchable knowledge reservoir" stage. The repo has:

- local-first markdown vault as source of truth
- hybrid retrieval across exact, BM25, semantic, archive, and graph channels
- trust/freshness computation and reranking
- maintenance jobs and review queues
- agent bridge scripts for Codex and Claude workflows

The next milestone is to turn that substrate into a reusable operating layer:

1. agent checks MnemonicOS before major LLM work
2. MnemonicOS returns a bounded memory packet plus trust/freshness guidance
3. agent uses, verifies, or ignores memory based on policy
4. agent records outcomes, failures, and durable learnings
5. MnemonicOS updates retrieval quality, review queues, and reusable notes over time

The architecture should stay two-speed:

- fast path: retrieval, packet assembly, writeback intake, lightweight policy decisions
- slow path: dedupe, relinking, freshness recompute, contradiction checks, consolidation, review generation

This allows MnemonicOS to become self-organizing without forcing heavyweight work into the agent's request path.

### Key Changes

#### 1. Formalize MnemonicOS as an agent-facing runtime protocol

Define MnemonicOS as a versioned interface independent of Codex, Claude Code, or future agent shells.

Core interaction contract:

- `resolve_task_context`
  - Inputs: task text, task kind, query intent, temporality, token budget, optional workspace/project scope
  - Output: retrieval state, recommendation, top memory packet, related memory links, verification guidance
- `record_outcome`
  - Inputs: task id/session id, outcome type, success/failure, summary, sources checked, memory usefulness labels
  - Output: writeback recommendation and update targets
- `decide_writeback`
  - Inputs: structured session summary and candidate existing notes
  - Output: `skip`, `confirm`, `update`, `new`, `incident_only`, `procedure_refinement`
- `run_maintenance`
  - Inputs: job name and dry-run/live mode
  - Output: freshness changes, review items, relearn items, eval candidates

Tracking requirement:

- `task_id` and `session_id` must be defined consistently across agent bridges, or writeback tracking, usefulness signals, and dedupe will become noisy.

Agent behavior policy:

- retrieve before meaningful LLM work when task is non-trivial
- reuse directly only when retrieval state is current enough for the query type
- if stale/contested or no-hit, do normal repo/tool/web research
- after first meaningful failure, retrieval is mandatory before another retry
- write back only durable knowledge, reusable procedures, meaningful failures, and notable decisions
- avoid duplicate note creation by preferring `confirm` then `update` then `new`

Public interface additions:

- enrich existing CLI/API responses with `recommendation`, `verification_action`, `memory_packet`, and `related_memories`
- keep current scripts and minimal API as compatibility surface; layer the richer protocol on top rather than replacing them

#### 2. Define schema overlays and lifecycle states without breaking current note kinds

Keep current durable note kinds as the storage taxonomy for the next phase. Current code already depends on:

- `person`
- `project`
- `repo`
- `decision`
- `procedure`
- `concept`
- `incident`
- `source`
- `journal`
- `overview`

Planned schema changes must stay explicitly aligned with `SUPPORTED_NOTE_KINDS` plus current ingestion/promotion logic. Do not treat this phase as a clean-slate taxonomy replacement.

Short-term approach:

- introduce `fact` and `episode` as logical schema overlays or retrieval facets, not immediate durable note kinds
- map `fact` mainly onto `concept` and `source`, with limited contextual overlap into `decision`
- map `episode` mainly onto `incident` and `journal`, with links to `procedure` and `decision`

Lifecycle states:

- `draft`
- `review`
- `verified`
- `canonical`
- `contested`
- `archived`

Freshness remains separate and keeps current values:

- `fresh`
- `aging`
- `suspect`
- `stale`
- `contested`

Policy distinction:

- freshness answers "how temporally safe is this"
- lifecycle answers "how authoritative is this in the library"

Required metadata across notes and overlays:

- stable id
- title
- durable note kind
- optional overlay class
- summary
- source provenance
- created_at, updated_at, last_verified_at, last_used_at
- confidence
- trust/usefulness signals
- freshness state
- lifecycle state
- project/workspace scope when relevant
- related note ids and typed links

Note type alignment / migration plan:

- after schema overlay definition, add a compatibility review step that documents how each new overlay maps to current note kinds
- if `fact` or `episode` are later promoted into first-class durable kinds, treat that as an explicit future migration requiring coordinated code, vault, parser, graph, retrieval, tooling, and test updates

#### 3. Add typed weighted linked-memory navigation as a first-class subsystem

Treat memory structure as a multi-relational graph, not a tree.

Explicit relation types to support:

- `about`
- `part_of`
- `depends_on`
- `before`
- `after`
- `caused_by`
- `similar_to`
- `contrasts_with`
- `derived_from`
- `refines`
- `supersedes`
- `evidence_for`
- `related_to`

Storage policy:

- explicit links in note frontmatter/body remain source of truth
- derived graph links remain rebuildable cache
- semantic similarity and contrast edges may be inferred but must be marked as derived, not canonical

Edge weighting inputs:

- relation type prior
- source note trust
- source and target freshness
- recency for timeline edges
- query intent match
- repeated co-retrieval/usefulness signals
- optional manual boost for curated links

Retrieval behavior:

- retrieval finds seed notes first via exact/BM25/semantic
- graph expansion runs from top focal notes
- neighbors are reranked by relation type, trust, freshness, and intent
- output keeps linked memories grouped by relation rather than flattening all results into one list

Navigation packet shape:

- `primary_memories`
- `related_by_topic`
- `related_by_timeline`
- `related_by_similarity`
- `related_by_contrast`
- `warnings`
- `verification_needed`

Design goal:

- if the agent finds one good note, it should immediately have a reliable path to nearby supporting notes, prior failures, contrasting outdated procedures, and newer superseding knowledge

#### 4. Upgrade retrieval into bounded memory packets

The product of retrieval should be a compact, policy-aware packet for the next LLM step, not just a ranked note list.

Memory packet contents:

- top actionable facts
- top relevant procedures
- top episodes or failures
- freshness/trust warnings
- relation-aware follow-up notes
- explicit "safe to use / verify first / research fresh" guidance
- provenance summary for each included item

Budget controls:

- max notes per type
- max related notes per relation bucket
- max total token budget
- summary-first, raw excerpts only when justified
- historical-only items excluded unless temporality asks for history or current memory is weak

Decision policy:

- `reuse`
  - current, trusted, sufficient
- `reuse_with_verification`
  - partially sufficient or slightly aged
- `research_fresh`
  - no hit, stale, contested, or clearly current-truth sensitive

This packet format should power both script bridges and future API clients.

#### 5. Make writeback, consolidation, and conflict handling explicit

The memory OS should improve itself through disciplined writeback, not passive note accumulation.

Agent boundary:

- agents should do minimal MnemonicOS-specific work
- agents query memory, submit outcomes, and provide light structured summaries when natural
- MnemonicOS owns normalization, categorization, linking, trust/freshness updates, dedupe, conflict checks, consolidation, and review queues

Writeback inputs:

- task outcome
- summary of steps taken
- errors/failures encountered
- sources checked
- existing candidate notes
- whether fresh research was required

Writeback decisions:

- `skip`
  - transient or not reusable
- `confirm`
  - strengthens an existing note without changing core content
- `update`
  - revises an existing note with newer evidence
- `new`
  - create a new durable note only when coverage is missing
- `incident_only`
  - record failure/lesson without promoting to canonical knowledge
- `procedure_refinement`
  - improve an existing runbook or procedure from new experience

Consolidation pipeline:

- capture episode
- extract lessons
- compare against existing facts/procedures/decisions
- merge or contest based on source quality and recency
- create review tasks when confidence is medium or conflict exists
- promote repeated successful patterns into canonical procedures

Conflict rules:

- do not silently overwrite conflicting durable knowledge
- newer evidence wins only when source quality is comparable or better
- preserve context-specific variants if both may be valid
- mark notes as `contested` when conflict is unresolved
- create explicit review items and link the competing notes

#### 6. Clarify working memory boundaries

Working memory is part of the runtime, not the durable vault.

Working memory responsibilities:

- hold immediate task context
- track current hypotheses, active subtasks, temporary observations
- support iterative refinement within one task/session

Rules:

- working memory is ephemeral by default
- only durable distilled outputs enter MnemonicOS long-term storage
- no direct promotion from scratchpad to canonical note without structured writeback path

#### 7. Use deterministic organization by default, with optional small-LLM assistance

MnemonicOS should own self-organization, but not depend on heavyweight agent participation.

Use deterministic logic by default for:

- schema validation
- note type alignment
- id generation
- dedupe heuristics
- explicit link extraction from structured fields
- freshness calculation
- trust updates
- graph maintenance
- conflict detection rules
- packet assembly and token budgets

Use a small independent LLM only as an assist layer for:

- summarizing messy raw session text
- suggesting typed links when explicit structure is weak
- extracting candidate lessons from episodes
- proposing merge/refinement candidates
- generating compact synthesis drafts for review

Control rule:

- deterministic system owns state transitions
- small LLM produces candidates, not authoritative mutations
- ordinary task agents should not be responsible for MnemonicOS organization quality

#### 8. Support callable now, service-ready later runtime topology

MnemonicOS should not require a permanent daemon in the first pass, but it should be designed so that one can be added later.

Recommended progression:

1. local callable engine for retrieval and writeback decisions
2. local scheduled maintenance jobs for self-organization
3. optional always-on daemon/service for lower coordination overhead
4. optional distributed workers/services for future scale

Operational split:

- fast path stays synchronous for agent calls
- slow path runs asynchronously for organization, maintenance, consolidation, and revalidation

This keeps latency low while still enabling self-organization.

#### 9. Expand evaluation and benchmarking around actual system goals

Measure MnemonicOS by its effect on agent efficiency and task quality.

Primary metrics:

- retrieval hit rate by task kind
- memory packet usefulness rate
- token reduction per completed task
- average number of LLM calls per task
- retrieval latency
- repeated-error reduction
- task success rate
- rate of duplicate note creation avoided
- fraction of stale/contested knowledge caught before misuse

Benchmark scenarios:

- direct LLM workflow without MnemonicOS
- MnemonicOS retrieval only
- MnemonicOS retrieval plus linked-memory expansion
- MnemonicOS retrieval plus writeback over repeated tasks

Evaluation dataset additions:

- architecture questions
- coding/debugging tasks
- repeat procedural tasks
- stale-knowledge scenarios
- contradiction/conflict scenarios
- no-hit tasks that should create reusable new memory

### Relevant Files

- `README.md` - current architecture and product-level vision
- `docs/IMPLEMENTATION_PLAN.md` - current implementation baseline and minimal runtime API
- `docs/codex-integration.md` - current Codex bridge semantics
- `src/second_brain/retrieval.py` - hybrid retrieval and reranking
- `src/second_brain/trust.py` - trust scoring, freshness, and rerank training
- `src/second_brain/graph.py` - note graph materialization and expansion
- `src/second_brain/ingest.py` - ingestion, normalization, promotion, and update paths
- `src/second_brain/bridge.py` - bridge-level retrieval and writeback logic

### Verification

1. Confirm the new runtime protocol layers cleanly onto the existing scripts and minimal API instead of duplicating them.
2. Validate that schema overlay planning does not conflict with `SUPPORTED_NOTE_KINDS` or existing promotion behavior.
3. Check that typed link additions can be represented as explicit source-of-truth note metadata plus rebuildable graph cache edges.
4. Confirm that `task_id` and `session_id` semantics can be made consistent across Codex and Claude bridges.
5. Validate the benchmark plan against current retrieval logs and `evals/candidates.jsonl`.

### Decisions / Assumptions

- The repo's current Phase 4 substrate remains the foundation: markdown vault source of truth, rebuildable caches, hybrid retrieval, graph expansion, trust/freshness scoring, maintenance jobs, and bridge scripts.
- The next phase prioritizes formalization and protocol design over replacing the current retrieval engine.
- MnemonicOS remains local-first and model-agnostic.
- Explicit note links are authoritative; inferred similarity/contrast links are cache-level aids.
- Working memory is runtime-only unless explicitly distilled through structured writeback.
- Retrieval should optimize for lower token usage and fewer model calls, not maximum context recall.
- `fact` and `episode` are planned as overlay classes first, not immediate additions to durable note kinds.

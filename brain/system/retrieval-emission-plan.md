# Retrieval Emission Plan — Stage 7

## Purpose

Stage 7 consumes an `AssessedPacket` / `ResolveTaskContextResult` and renders it
for callers as stable JSON and Markdown. It is the presentation boundary of the
retrieval path: it exposes the selected memory, assessment directive, provenance,
warnings, archive evidence, and follow-up policy without redoing assessment or
retrieval. When Stage 6 says the packet is incomplete in a thresholded way,
Stage 7 also emits the caller-facing focused requery instruction that may start
a new retrieval pass outside the pipeline.

Stage 7 is mostly a lightweight formatting layer, but not a trivial string
printer. It must preserve the semantics of Stage 6's `AssessmentDirective`,
avoid leaking deferred or unsafe branches, keep JSON and Markdown outputs
equivalent, and make instrumentation clearly separate from canonical memory
writes.

Stage 7 answers: **how should assessed memory be emitted to this caller right
now?** It does not search, rank, re-assess, mutate wiki state, or invent a new
recommendation.

Implementation details live in the phase files:

1. [Phase 1: Emission Contract and Output Profiles](retrieval-emission-plan-phase1.md)
2. [Phase 2: JSON Renderer Parity](retrieval-emission-plan-phase2.md)
3. [Phase 3: Markdown Renderer and Full Text Presentation](retrieval-emission-plan-phase3.md)
4. [Phase 4: Archive, Warning, and Contradiction Presentation](retrieval-emission-plan-phase4.md)
5. [Phase 5: Directive-Guided Branch Filtering](retrieval-emission-plan-phase5.md)
6. [Phase 6: Metrics and Retrieval Trace Emission](retrieval-emission-plan-phase6.md)
7. [Phase 7: Shell/API Consolidation and No-Raise Degradation](retrieval-emission-plan-phase7.md)

---

## Boundary

```text
AssessedPacket / ResolveTaskContextResult
  -> select render profile from AssessmentDirective
  -> filter packet branches using selected/deferred IDs
  -> render stable JSON and Markdown
  -> expose provenance, warnings, archive evidence, and follow-up policy
  -> log emission/runtime metrics
```

Stage 7 owns:

- output schema stability for JSON and Markdown
- rendering `assessment_directive` fields without reinterpretation
- applying `emission_profile` and selected/deferred branch IDs
- presenting `archive_context` separately from canonical wiki memory
- presenting warnings, contradictions, missing roles, and verification action
- rendering `fresh_pipeline_threshold` and `followup_query_policy`
- composing focused follow-up query instructions for the caller
- exposing body references or pre-budgeted text according to `hydration_policy`
- preserving caller-facing compatibility fields such as `retrieval_state` and
  `recommendation`
- no-raise degraded output for empty or malformed packets
- emission and runtime packet metrics

Stage 7 does not own:

- query parsing
- recall, graph expansion, archive search, or fresh pipeline reruns
- signal integration, trust, freshness, importance, or quality scoring
- reranking
- packet assembly, trace completion, body budgeting, or branch construction
- trustworthiness or completeness assessment
- deciding whether to execute a follow-up query
- recursive Stage 2-6 loops
- canonical wiki writes or writeback decisions

---

## Input Contract

Primary input is the Stage 6 handoff:

```text
AssessedPacket:
  packet: MemoryPacket
  directive: AssessmentDirective
```

Runtime paths may expose the equivalent `ResolveTaskContextResult`, which
already contains:

- compatibility fields: `retrieval_state`, `recommendation`,
  `verification_action`
- `assessment_directive`
- `memory_packet`
- `related_memories`
- `provenance_summary`
- debug fields: query ID, query type, classifier confidence, persona, back-feed
  hook

Stage 7 must treat `AssessmentDirective` as authoritative. If compatibility
fields disagree with the directive, that is a degraded emission condition and
should be surfaced in metrics; Stage 7 should prefer the directive.

---

## Emission Profiles

Stage 6 already chooses the profile. Stage 7 only formats accordingly.

| Profile | Presentation |
|---------|--------------|
| `concise_success_path` | selected current reusable path, core provenance, brief warnings |
| `success_plus_major_failures` | selected success path plus major failure/caution branches |
| `full_chain` | functional path or selected reservoir branches in explanatory order |
| `warning_only` | warnings, contradictions, missing roles, verification/fresh-research action |
| `no_memory` | explicit no-memory result with fresh-research recommendation |

The renderer must never emit the whole assessment reservoir merely because it is
available. Deferred branches are represented as a compact follow-up summary
unless `emission_profile = full_chain` or the directive selects them.

---

## Focused Requery Handoff

Stage 6 may decide that the current packet is incomplete in a thresholded way:

```text
fresh_pipeline_threshold = missing_required_role
followup_query_policy = immediate_focused_requery
missing_roles = ["success_resolution"]
```

Stage 7 must not satisfy this by rerunning the retrieval pipeline internally.
Instead, it emits a structured handoff to the caller:

```text
followup_query:
  policy: immediate_focused_requery
  threshold: missing_required_role
  missing_roles: ["success_resolution"]
  anchors:
    original_intent: "<original task intent>"
    note_ids: ["incident/auth-rollout-failure", "decision/auth-rollout-decision"]
    chain_id: "trace/auth-rollout-recovery"
    branch_id: "branch/current-recovery"
    error_signature: "<stable error signature, if any>"
    relation_target: "procedure/recover-auth-rollout"
  suggested_query: "auth rollout recovery success_resolution chain_id:trace/auth-rollout-recovery procedure/recover-auth-rollout"
```

The caller, bridge, agent policy, or human decides whether to issue that query.
If it does, the next pass starts from Stage 1 as a normal fresh retrieval call.
This keeps each retrieval pass bounded, observable, and debuggable.

An orchestrator may run this follow-up automatically, but that automatic retry
is outside Stage 7 itself. Stage 7's job is to emit a structured
`followup_query` instruction plus enough retry metadata for the orchestrator to
make a safe decision.

Stage 7 should generate the suggested query from information already present in
the directive and packet:

- original task/query intent when available
- `missing_roles`
- `fresh_pipeline_threshold`
- stable note IDs for selected and relevant deferred packet items
- relevant `chain_id` and `branch_id`
- key error signature, failure text, or test/log signature when present
- relation target IDs when Stage 5/6 knows the missing path points toward a
  specific procedure, decision, incident, or concept
- verification/freshness action when it changes the query intent

Suggested queries are instructions, not commands. They must be explicit enough
for an agent to reuse but visibly optional and caller-mediated. Prefer stable
anchors over prose synonyms so the next Stage 1-2 pass can recover the intended
relation path.

Example Markdown:

```text
Found partial memory:
- incident/auth-rollout-failure explains the failed rollout.
- decision/auth-rollout-decision explains the pivot.

Missing required role:
- success_resolution

Suggested focused MnemonicOS query:
"auth rollout recovery success_resolution chain_id:trace/auth-rollout-recovery procedure/recover-auth-rollout"
```

Policy handling:

| `followup_query_policy` | Stage 7 behavior |
|-------------------------|------------------|
| `none` | no follow-up query instruction |
| `on_first_failure` | emit deferred follow-up condition and focused query sketch |
| `immediate_focused_requery` | emit explicit focused requery instruction before relying on packet as complete |

---

## Automatic Retry Policy

Automatic focused retry is useful for bridge and agent callers because it can
turn a partial packet into a complete one without bothering the user. It should
be implemented as caller/orchestrator control flow around the retrieval
pipeline, not as recursion inside Stage 7.

Allowed shape:

```text
Pipeline pass 1: Stage 1 -> ... -> Stage 7
Stage 7 emits followup_query + retry metadata
Orchestrator decides retry is allowed
Pipeline pass 2: Stage 1 -> ... -> Stage 7 using suggested_query
Orchestrator returns a composed multi-pass response
```

Stage 7 may set retry metadata:

```text
retry_control:
  auto_retry_allowed: true
  max_focused_retries: 1
  retry_attempt: 0
  stop_if_no_new_ids: true
  stop_if_same_threshold: true
  stop_if_same_missing_roles: true
  cutoff_reason: null
```

Default policy:

- automatic focused retry is disabled for human-facing CLI unless explicitly
  requested
- bridge/agent callers may enable one automatic focused retry
- maximum focused retries defaults to `1`
- hard maximum is `2` only for debugging tasks with a new concrete error
  signature between attempts
- every automatic retry must produce an observable query ID and trace event

Hard cutoff conditions:

- the focused query returns no packet content
- the focused query returns no new stable note/archive IDs compared with prior
  attempts
- the focused query repeats the same `fresh_pipeline_threshold` and
  `missing_roles`
- the suggested query is identical to a previous retry query
- the retry budget is exhausted
- Stage 6 returns `insufficient_no_hit`, `insufficient_stale`, or
  `insufficient_contested` after the focused pass

When a cutoff triggers, Stage 7 should emit final bounded output:

```text
Memory remains partial after focused retry.
Cutoff: no_new_ids
Do not keep querying automatically.
Proceed with available memory or do fresh external research.
```

This avoids an indefinite "more queries" loop when the memory simply does not
exist.

---

## Multi-Pass Response Composition

If an orchestrator performs an automatic focused retry, the caller should receive
both Stage 7 emissions in one composed response. The focused pass may be marked
as the final answer candidate, but the first pass must remain visible as trace
and provenance.

Recommended JSON shape:

```text
retrieval_passes:
  - pass_index: 1
    query_id: 123
    query_text: "<original task>"
    emission: {... Stage 7 output ...}
    triggered_followup_query: {...}
  - pass_index: 2
    query_id: 124
    query_text: "<focused suggested query>"
    emission: {... Stage 7 output ...}
    cutoff_reason: null
final_pass_index: 2
auto_retry_summary:
  attempted: true
  attempt_count: 1
  new_ids: ["procedure/recover-auth-rollout"]
  cutoff_reason: null
```

If the focused retry hits a cutoff, return both passes and mark the cutoff:

```text
final_pass_index: 2
auto_retry_summary:
  attempted: true
  attempt_count: 1
  new_ids: []
  cutoff_reason: no_new_ids
```

Markdown should not duplicate every detail from both emissions by default. It
should present the final/focused pass first, then a compact "Retry Trace"
section showing:

- pass 1 query ID and follow-up query emitted
- pass 2 query ID and whether new IDs were found
- cutoff reason, if any

This preserves observability without making the response unreadable.

---

## Full Text Presentation

"Full text" in Stage 7 means complete presentation of the selected packet, not
unbounded note hydration. The packet should remain the source of truth for what
is allowed to appear.

Recommended rule:

```text
Stage 5 decides what text is available and budget-safe.
Stage 6 decides which branches/profile are eligible.
Stage 7 renders the eligible text and references faithfully.
```

If Stage 7 later gains a body resolver for normal emission, it must be a narrow
read-only adapter that obeys `hydration_policy`, packet body references, and
budget metadata. It must not broaden the memory set, follow links, or silently
replace Stage 5's budget decisions.

Direct retrieval now has a separate opt-in inspection affordance:
`mnemonicos retrieve --include-body [--body-chars N]` and the matching HTTP
`include_body` / `body_chars` fields hydrate returned wiki hits from their
`body_path` files after ranking. This is not Stage 7 normal emission, does not
change recall/ranking/assessment, and does not activate deep research mode. It
exists so humans and debugging tools can inspect the exact selected wiki note
text while compact retrieval remains the default.

---

## Output Contract

JSON output should be canonical and machine-first. Markdown output should be
human/agent-readable and derived from the same render model.

Required JSON sections:

- protocol/version/operation
- compatibility fields: `retrieval_state`, `recommendation`,
  `verification_action`
- `assessment_directive`
- selected packet slots
- `archive_context`
- warnings, contradictions, missing roles, verification IDs
- deferred branch summary and follow-up policy
- focused requery instruction when requested by the directive
- retry control and cutoff reason when automatic retry is enabled or exhausted
- multi-pass response trace when automatic retry was attempted
- provenance/debug block

Required Markdown sections:

- `## Memory Context`
- directive summary: state, recommendation, verification action, completion
  state, emission profile
- selected memory sections in Stage 5 explanatory order
- archive evidence section, explicitly labeled raw/archive
- warnings/contradictions section when present
- deferred context and follow-up policy when present
- focused follow-up query instruction when present
- retry cutoff notice when no further automatic focused query should run
- retry trace when the response includes more than one Stage 7 pass
- provenance/debug footer when appropriate for the caller

Markdown should be stable enough for bridge prompts, but JSON is the canonical
schema for tooling and tests.

---

## Caveats and Cautions

- Stage 7 is lightweight, but it is security- and semantics-sensitive.
- Formatting must not collapse `retrieval_state`, `completion_state`,
  `reuse_mode`, and `verification_action` into one vague prose message.
- Archive evidence must be visibly non-canonical and never mixed into primary
  memory.
- Deferred branches must stay deferred unless selected by the directive.
- Warning-only and no-memory emissions must be explicit, not empty output.
- Requery-needed emissions must be explicit, not hidden recursive work.
- Automatic retry, when enabled by a caller/orchestrator, must have a small
  retry budget and hard cutoff conditions.
- Automatic retry responses must preserve every Stage 7 pass; no pass output is
  silently discarded.
- Markdown and JSON must be generated from the same render model to avoid
  shell/API drift.
- Instrumentation is allowed; canonical wiki mutation is not.
- No renderer should call an LLM to polish or summarize the packet.
- Emission must degrade gracefully even when packet slots are empty, malformed,
  or internally inconsistent.

---

## Non-Negotiable Invariants

- Stage 7 never reclassifies trustworthiness or completeness.
- Stage 7 never runs recall, reranking, trace completion, or fresh research.
- Stage 7 never starts an internal second retrieval pass.
- Stage 7 may emit retry metadata for an external orchestrator.
- Stage 7 never writes canonical wiki state.
- `AssessmentDirective` is authoritative for profile, branch selection,
  verification action, and follow-up policy.
- JSON and Markdown expose equivalent semantic content.
- `archive_context` remains separate from wiki memory in every output format.
- Empty output is forbidden; emit `no_memory` or `warning_only` explicitly.
- `immediate_focused_requery` is emitted as a caller instruction, not executed.
- No automatic retry loop may continue after retry budget exhaustion or a
  no-new-IDs focused result.
- Multi-pass responses must identify `final_pass_index` and retain pass-level
  query IDs.
- Emission metrics are instrumentation only.

---

## Deep Research Mode (Future — Design Placeholder)

> **Status:** Not implemented. This section captures design intent for when
> full-body hydration and deep research emission become active. Do not implement
> until the deep research phase is formally planned. Reference
> `hydration_policy = hydrate_full_functional_path` and the `full_chain`
> emission profile, which are currently unreachable dead code.
>
> **Clarification:** the implemented `--include-body` / `include_body` retrieval
> inspection path is not this mode. It hydrates only the already-returned wiki
> hits for explicit caller inspection and remains subject to normal top-k
> selection.

### What the Mode Is

Deep research is a distinct emission mode, not a condition silently triggered by
the normal Stage 6 assessment path. It is characterized by:

- Full note body hydration (reading `body_path` files, not just summaries)
- No binding top-k cap — output is unbounded or operates under a very
  permissive threshold
- `emission_profile = full_chain` and `hydration_policy = hydrate_full_functional_path`
  become live paths

### How It Is Activated

Two entry points, both explicit:

**1. Manual — CLI flag (user-initiated):**
```bash
mnemonicos retrieve "query" --mode deep-research
```
The caller explicitly opts into expensive, unbounded output. No automatic
activation from assessment signals alone.

**2. Self-triggered — pipeline condition (system-initiated):**
Specific Stage 6 conditions may recommend deep research mode when the caller
has signaled willingness (e.g., a bridge setting `allow_deep_research = true`).
Candidate trigger conditions (exact list deferred to implementation phase):
- `completion_state = fragmentary` AND task_kind = `debugging` AND caller allows
- explicit `--deep-research` hint passed as `QueryHints` from the bridge
- multi-pass retry exhausted normal budget and a deep pass is permitted

Self-triggering must never happen without caller consent. Stage 7 in normal mode
emits a recommendation to retry with `--mode deep-research` rather than
silently escalating cost.

### Emission Behavior When Active

When deep research mode is active:

- Token cap is removed or raised to a caller-specified limit. Default threshold
  TBD at implementation time — should be very accepting, not the compact-mode
  6000-token limit.
- All selected packet branches are hydrated from `body_path` files (read-only,
  wiki files only).
- `max_hits` cap from Phase 4 dynamic top-k does not apply; instead, a
  separate `deep_research_max_hits` config param governs the ceiling.
- Archive context may be included alongside wiki memory in the full text view,
  still labeled separately.
- `full_chain` profile presents the full functional path in explanatory order
  including all failure, pivot, and caution branches.

### Exact Design Is Deferred

The specific token ceiling, `deep_research_max_hits` value, body resolver
implementation, self-trigger conditions, and deep-research CLI surface (mode
flags, config keys, bridge protocol fields) are to be designed when this mode is
planned. Do not guess or hardcode values here. Existing direct-retrieve
inspection flags (`--include-body`, `--body-chars`) are intentionally narrower
and should not be treated as a deep-research API.

What is fixed by this placeholder:
1. Deep research is a **named mode**, not an implicit escalation.
2. Manual activation via CLI flag is a first-class requirement.
3. Self-triggering requires explicit caller consent, not just pipeline state.
4. Output under deep research is unbounded or permissively capped — the normal
   `max_hits = 15` and 6000-token limit do not apply.

---

## Completion Gate

Stage 7 is complete when:

1. A documented render model maps `AssessmentDirective + MemoryPacket` to JSON
   and Markdown.
2. Direct `retrieve()` output, `resolve_task_context`, shell output, and HTTP
   output expose compatible assessment and archive fields.
3. Markdown and JSON renderers share the same selected packet/filtering logic.
4. Archive context, warnings, contradictions, deferred branches, missing roles,
   and follow-up policy are covered by fixtures.
5. `immediate_focused_requery` and `on_first_failure` fixtures emit focused
   query handoffs without internal recursion.
6. Automatic retry metadata includes max attempts and cutoff conditions.
7. No-new-entry and same-threshold focused retry fixtures terminate with a
   cutoff notice.
8. Automatic retry responses include both Stage 7 emissions with pass-level
   query IDs and `final_pass_index`.
9. Runtime packet metrics include directive-specific fields without canonical
   wiki writes.
10. Empty, warning-only, archive-only, malformed, and overabundant packets render
   without raising.
11. Stage 7 tests prove no reassessment or packet broadening occurs during
   emission.
12. Direct retrieve inspection hydration, when requested, is additive,
    post-selection, read-only, and absent by default.

---

## Reference

**Roadmap:** [retrieval-emission-roadmap.md](retrieval-emission-roadmap.md)  
**Stage 6 input:** [retrieval-assessment-plan.md](retrieval-assessment-plan.md)  
**Stage 5 packet source:** [retrieval-assembly-plan.md](retrieval-assembly-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)

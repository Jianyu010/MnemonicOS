# Retrieval Assembly Phase 4: Related Context Slots and Bounded Trace Completion

## Goal

Route non-primary context into meaningful slots so the assembled note is
coherent instead of a flat list of neighbors. When the assembled context only
contains fragments of a process chain, complete the trace through explicit
relations in a bounded, deterministic way and preserve the result in an
assessment reservoir for Stage 6.

## Scope

- `related_by_topic`
- `related_by_timeline`
- `related_by_similarity`
- `related_by_contrast`
- relation-family mapping
- functional path reconstruction from relation families
- bounded trace completion over explicit relation edges
- branch construction and functional role labeling
- assessment reservoir population and default/deferred branch marking

## Components

```text
src/second_brain/runtime.py      — eventual relation routing
src/second_brain/graph.py        — relation families
src/second_brain/recall.py       — candidate relation provenance
src/second_brain/models.py       — reservoir / trace metadata fields
tests/test_runtime_protocol.py   — trace-completion fixtures
```

## Slot Rules

1. Timeline relations include before/after/supersedes/superseded_by and
   episode-like incidents or journals.
2. Similarity relations are supporting context, not authority.
3. Contrast relations are useful when they distinguish alternatives or warn
   against an outdated approach.
4. Topic relations collect graph-adjacent and general related material that
   does not belong in a more specific slot.

## Trace Completion Rules

Trace completion is a local assembly pass, not a second recall cycle. It starts
from admitted Stage 5 seeds: primary memories, same-episode items, related
items, warning-only wiki items, and explicit relation endpoints already present
in ranked metadata. It may add only canonical wiki notes reachable through
allowed maintenance relations.

Although the operation follows relation edges, it belongs here rather than in
Stage 2 because its job is not candidate discovery. Stage 2 traces broadly
enough to create a candidate pool. Stage 5 trace completion repairs a selected
assembly for a known caller purpose. It should add the few relation endpoints
needed to complete or clarify that purpose-specific path, not every linked note
available from the seed.

Purpose-awareness is deterministic and software-owned. The trace pass does not
call an LLM. It derives a `TracePurpose` profile from Stage 1 query type,
temporality, caller/task kind, query anchors, primary note type, current
functional-path gaps, and packet warnings/contradictions. Endpoint admission is
then a fixed predicate over relation family, role usefulness, anchor/branch
match, and cap status.

```text
TracePurpose:
  intent: implementation|debugging|historical|comparison|factual
  required_roles: list[functional_role]
  optional_roles: list[functional_role]
  low_value_roles: list[functional_role]
  terminal_preference: success|diagnosis|timeline|contrast|none
```

Allowed relation families:

| Family | Relations | Typical role contribution |
|--------|-----------|---------------------------|
| episode | `chain_id`, session lineage | same process branch |
| timeline | `before`, `after` | ordering, prerequisite, follow-up |
| replacement | `supersedes`, `superseded_by` | current path vs obsolete branch |
| causal/debug | `failed_due_to`, `attempts_to_solve`, `supports_solution` | failure, diagnosis, resolution |
| derivation | `derived_from` | prerequisite, decision, procedure lineage |
| workflow | incident/decision/procedure/concept links | goal, pivot, implementation path |

Disallowed actions:

- no BM25, semantic, archive, or embedding-neighbor search
- no fresh Stage 2-4 ranking pass
- no unbounded graph walk
- no all-links expansion from a seed or chain
- no same-chain flood fill
- no promotion of archive chunks into canonical slots
- no mutation of notes, relations, or maintenance tables

## Trigger Rules

Run trace completion when at least one trigger is present:

1. A selected item has `chain_id` and the current path lacks a terminal state.
2. The parsed query intent is procedure, implementation, debugging, or repair,
   and the path has success without prerequisites or failure without
   diagnosis/resolution.
3. A selected item exposes one of the allowed path-forming relations.
4. The preliminary path confidence would be `partial` or `none` despite having
   path-like candidates.

Skip trace completion for flat factual lookups, person/source lookups, and
queries where no explicit relation edge indicates a process chain.

Purpose filter:

| Caller intent | Trace may add | Trace should avoid |
|---------------|---------------|--------------------|
| implementation/procedure | prerequisites, decisions, current success path, major cautions | unrelated incidents, old alternatives, full history |
| debugging/repair | failed attempt, diagnosis, fix, regression/caution | successful branches unrelated to the error shape |
| historical | before/after, supersession, terminal state | current-only procedure branches unless needed for contrast |
| comparison/choice | contrasting alternatives and supersession evidence | same-chain procedural detail unrelated to the choice |

Every trace-added item must satisfy at least one admission reason:

- fills a missing functional role in the active branch
- establishes ordering needed to avoid causal confusion
- selects between current and superseded paths
- exposes a major warning, contradiction, or unresolved state needed by Stage 6
- connects a success/failure fragment already admitted into one coherent branch

Admission predicate:

```text
admit(endpoint) =
  relation in allowlist
  AND within trace caps
  AND purpose.intent != factual
  AND endpoint_role in purpose.required_roles + purpose.optional_roles
  AND (
    endpoint shares query/branch anchors
    OR relation is strong causal/supersession evidence
  )
  AND endpoint satisfies at least one admission reason
```

## Caps and Stop Conditions

Default caps:

```text
max_trace_depth: 2
max_added_wiki_items: 8
max_branches: 3
max_items_per_branch: 6
max_superseded_or_caution_branches: 2
max_added_archive_items: 0
```

Stop expanding a branch when:

- it reaches a terminal success, terminal failure, or unresolved follow-up
- the next edge would exceed depth or item caps
- the next endpoint is stale/contested and only useful as warning context
- two candidate edges conflict and Phase 7 needs to expose the contradiction

Cap pressure is recorded in `assembly_notes.trace_budget_limited` with omitted
relation families and counts.

Caps do not authorize noisy expansion. If an edge endpoint does not satisfy the
purpose filter, it is rejected before caps are considered.

## Branch Construction

Trace completion should build one or more branches, each with:

```text
branch_id
seed_item_ids
item_ids
edge_path
terminal_state: success|failure|mixed|unresolved|unknown
path_confidence: explicit|inferred|partial|none
default_emission_candidate: bool
defer_reason: overabundant|superseded|warning|low_confidence|not_query_intent
```

The branch order is explanatory:

```text
goal / question
-> failed attempts
-> diagnoses and constraints
-> decisions / pivots
-> successful resolution
-> current reusable procedure
-> cautions, superseded branches, unresolved follow-up
```

Role assignment precedence:

1. explicit relation type and endpoint note type
2. chain order and terminal state
3. freshness/lifecycle state
4. weak title/summary heuristic

Weakly inferred roles must lower `path_confidence`; they should never be
reported as explicit.

## Functional Ordering Rules

Stage 4 ranking is used to decide which related entries are admitted. Once
admitted, Stage 5 may reorder entries for readout when relation evidence
supports a functional path. Explicit relations win over inferred order.
`before`/`after`, `failed_due_to`, `attempts_to_solve`, `supports_solution`,
`derived_from`, `supersedes`, and incident/decision/procedure links should be
mapped to `assembly_role` and `assembly_order` rather than flattened into score
order.

Trace-added items keep both ranking and trace metadata. If the item was present
in Stage 4 ranked hits, preserve its score and channel provenance. If it was
added only by trace completion, mark `trace_source = relation_completion`,
record `edge_path`, and keep it out of `primary_memories` unless it was already
primary before the trace pass.

## Assessment Reservoir Rules

The reservoir contains all branches Stage 6 may choose from, including the
default branch and deferred branches. It is temporary packet state, not
canonical memory.

Default branch preference:

1. verified or canonical current success procedure
2. quality-passed success path with aging status plus cautions
3. explicit partial path with unresolved terminal state
4. archive-only evidence, if no canonical branch exists

Deferred branches are retained when they are superseded, cautionary,
low-confidence, off-intent, or too detailed for first emission. Stage 6 decides
whether deferred branches are emitted now, summarized, or held for
`on_first_failure` follow-up.

## Handoff Fields

Stage 6 needs to know:

- which relation family placed each item
- whether timeline context implies supersession or ordering risk
- whether contrast context suggests ambiguity or competing approaches
- which functional path role each item played, when known
- whether trace completion ran, skipped, or hit caps
- which branches exist in the assessment reservoir
- which branch is the default emission candidate
- which roles remain missing after trace completion
- which deferred branches may be useful after a concrete failure

## Verification

- relation families route deterministically to the same slot
- non-primary related items do not duplicate primary memories
- contrast and supersession relations remain visible for assessment
- success/failure histories are ordered by functional path, not raw rank, when
  relation evidence supports that reconstruction
- trace completion follows only allowlisted explicit relations
- trace completion rejects linked notes that do not satisfy the caller-purpose
  filter
- same-chain flood-fill fixtures do not admit all linked notes
- trace completion stops at depth, branch, and item caps
- success-only, failure-only, partial-chain, and overabundant fixtures produce
  distinct reservoir metadata
- trace-added items do not become primary solely because they complete a branch

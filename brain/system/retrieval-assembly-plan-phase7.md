# Retrieval Assembly Phase 7: Contradictions and Assessment Handoff

## Goal

Expose the packet conditions that Stage 6 needs to assess sufficiency,
freshness, and adjustment policy without re-running retrieval or reading the
database.

## Scope

- contradiction and supersession annotations
- stale/suspect/contested warnings
- archive-only and primary-empty conditions
- structured Stage 6 handoff fields
- functional path completeness and path-order confidence
- trace-completion status, branch metadata, and reservoir deferral reasons

## Components

```text
src/second_brain/runtime.py      — eventual handoff annotation
src/second_brain/models.py       — contradiction / assembly note fields
tests/test_runtime_protocol.py   — eventual assessment-handoff fixtures
```

## Handoff Rules

1. Contradiction evidence is structured, not only prose. It should identify
   source IDs, relation or signal type, and severity.
2. Supersession is not automatically a hard contradiction, but it is timeline
   evidence Stage 6 should consider before reuse.
3. Archive-only packets are not no-hit packets. They are raw-evidence packets
   with no canonical memory.
4. Warnings should explain why an item needs verification without deciding the
   final recommendation.
5. Functional path notes should tell Stage 6 whether the success/failure path is
   complete, partial, inferred, or contradicted. A clean path ending in a
   verified current procedure is different from a path with scattered failures
   and no confirmed resolution.
6. Trace completion is reported as packet metadata, not hidden inside prose.
   Stage 6 must be able to tell whether the reservoir is complete, capped,
   fragmentary, or overabundant without reading the database.
7. Deferred reservoir branches should carry a reason. Deferral means "not for
   first emission by default," not "unimportant."

## Proposed Assembly Notes

```text
primary_empty: bool
archive_context_present: bool
archive_only: bool
budget_limited: bool
body_deferred_item_ids: list[str]
warning_item_ids: list[str]
contradiction_count: int
supersession_item_ids: list[str]
deduped_item_ids: list[str]
functional_path_present: bool
functional_path_confidence: explicit|inferred|partial|none
functional_path_terminal_state: success|failure|mixed|unresolved|unknown
score_order_differs_from_assembly_order: bool
trace_completion_status: not_needed|completed|skipped|budget_limited|conflicted
trace_seed_item_ids: list[str]
trace_added_item_ids: list[str]
trace_omitted_relation_families: list[str]
trace_budget_limited: bool
assessment_reservoir_branch_count: int
assessment_reservoir_item_count: int
default_branch_id: str|null
deferred_branch_ids: list[str]
deferred_branch_reasons: dict[str, str]
missing_functional_roles: list[str]
```

## Reservoir Handoff Shape

Each reservoir branch should be inspectable by Stage 6:

```text
branch_id: str
seed_item_ids: list[str]
item_ids: list[str]
edge_path: list[RelationStep]
terminal_state: success|failure|mixed|unresolved|unknown
path_confidence: explicit|inferred|partial|none
default_emission_candidate: bool
defer_reason: overabundant|superseded|warning|low_confidence|not_query_intent|null
missing_roles: list[str]
warning_item_ids: list[str]
contradiction_ids: list[str]
```

Reservoir metadata must survive packet budgeting even if item excerpts are
trimmed. Body hydration remains Stage 7 work.

## Verification

- contested primary hits produce contradiction evidence for Stage 6
- supersession relation fixtures produce timeline handoff notes
- archive-only fixtures are distinguishable from total no-hit fixtures
- successful resolution paths and unresolved failure paths produce different
  terminal-state notes for Stage 6
- Stage 6 can classify packet state from packet fields alone
- trace-completed overabundant fixtures expose default and deferred branches
- capped trace fixtures expose `trace_budget_limited` and omitted relation
  families

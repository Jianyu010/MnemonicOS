# Retrieval Assessment Plan — Stage 6

## Purpose

Stage 6 consumes the Stage 5 `RawPacket` / evolved `MemoryPacket` and produces
an `AssessedPacket` with a structured `AssessmentDirective` for Stage 7
emission. It is the judgment layer: it decides whether the assembled memory is
safe enough to use, complete enough for the caller's immediate purpose, and how
much of the assessment reservoir should be shown now.

Stage 6 answers: **what should the caller do with this assembled memory?** It
does not fetch more memory, hydrate full bodies, format Markdown/JSON, or write
back canonical state.

Implementation details live in the phase files:

1. [Phase 1: AssessedPacket and AssessmentDirective Contract](retrieval-assessment-plan-phase1.md)
2. [Phase 2: Trustworthiness Classifier](retrieval-assessment-plan-phase2.md)
3. [Phase 3: Functional Completeness Classifier](retrieval-assessment-plan-phase3.md)
4. [Phase 4: Progressive Disclosure and Reservoir Selection](retrieval-assessment-plan-phase4.md)
5. [Phase 5: Fresh Pipeline Threshold Policy](retrieval-assessment-plan-phase5.md)
6. [Phase 6: Runtime and Shell Path Consolidation](retrieval-assessment-plan-phase6.md)
7. [Phase 7: Metrics, Degradation, and Stage 7 Handoff](retrieval-assessment-plan-phase7.md)

---

## Boundary

```text
RawPacket / MemoryPacket
  -> trustworthiness assessment
  -> functional completeness assessment
  -> reservoir branch selection
  -> reuse and verification policy
  -> AssessedPacket + AssessmentDirective
```

Stage 6 owns:

- classifying `retrieval_state`
- classifying `completion_state`
- deriving `reuse_mode`, recommendation, and verification action
- choosing an emission profile from the Stage 5 assessment reservoir
- deciding whether deferred branches should stay hidden until first failure
- declaring whether a fresh Stage 2-5 run is justified
- exposing missing functional roles and assessment reasons

Stage 6 does not own:

- candidate recall or graph tracing
- trust/freshness/quality signal computation
- ranking or slot selection
- bounded trace completion
- body hydration and final truncation
- Markdown/JSON rendering
- canonical wiki writes

---

## Two-Axis Assessment

Stage 6 evaluates the packet along two independent axes.

### Axis 1: Trustworthiness

Trustworthiness asks whether the memory is safe to use:

- primary contradictions
- contested/stale/suspect freshness state
- quality gate failures
- lifecycle state
- archive-only status
- primary/anchor relevance
- historical-only status
- supersession risk

### Axis 2: Functional Completeness

Completeness asks whether the packet is useful for the caller's immediate
purpose:

- required functional roles present
- success path present or missing
- failure/diagnosis path present when debugging
- prerequisites/decision path present when implementing
- timeline/supersession path present when historical
- reservoir overabundance requiring progressive disclosure
- missing roles after Stage 5 trace completion

A packet can be trustworthy but incomplete, complete but stale, or complete and
overabundant. Stage 6 must not collapse these cases into one enum.

---

## Retrieval State

Retrieval states retain existing compatibility but must be computed from all
primary memories, not only the top hit.

| State | Conditions |
|-------|------------|
| `insufficient_contested` | Primary contradictions block safe branch choice. |
| `insufficient_stale` | All primary memories are stale or historical-only for a current query. |
| `insufficient_no_hit` | No primary memories and no archive context. |
| `sufficient_historical` | Query temporality is historical and at least one primary memory is relevant. |
| `partial` | At least one primary memory fails quality, is suspect, is archive-only-actionable, or has unresolved assessment warnings. |
| `sufficient_current` | All primary memories are fresh/aging, quality-passed, non-contested, and relevant to query anchors. |

Historical packets recommend `reuse_with_verification`, not direct `reuse`.

---

## Completion State

| Completion state | Meaning |
|------------------|---------|
| `complete_path` | Required roles are present for the caller intent. |
| `success_only` | A reusable success path exists, but failure/caution context is thin. |
| `partial_chain` | Some process links exist, but important roles are missing. |
| `fragmentary` | Isolated hits exist but do not form an actionable path. |
| `overabundant` | The reservoir contains more branches/history than first response should emit. |

The classifier consumes Stage 5 fields:

- `functional_path`
- `assessment_reservoir`
- `assembly_notes.functional_path_*`
- `assembly_notes.missing_functional_roles`
- `assembly_notes.trace_completion_status`
- `assembly_notes.deferred_branch_ids`
- contradictions, warnings, and verification IDs

---

## AssessmentDirective

Stage 6 emits a directive for Stage 7:

```text
retrieval_state: sufficient_current|sufficient_historical|partial|insufficient_no_hit|insufficient_stale|insufficient_contested
completion_state: complete_path|success_only|partial_chain|fragmentary|overabundant
reuse_mode: direct_reuse|reuse_with_verification|scaffold_only|diagnose_on_failure|fresh_research_required
recommendation: reuse|reuse_with_verification|research_fresh
verification_action: none|verify_current_truth|check_recent_sources|treat_as_historical_only
emission_profile: concise_success_path|success_plus_major_failures|full_chain|warning_only|no_memory
hydration_policy: hydrate_primary_only|hydrate_primary_and_failure_branches|hydrate_full_functional_path|summaries_only
selected_branch_ids: list[str]
deferred_branch_ids: list[str]
missing_roles: list[str]
followup_query_policy: none|on_first_failure|immediate_focused_requery
fresh_pipeline_threshold: none|no_anchor|missing_required_role|new_error_signature|contradiction_blocks_use|archive_only_actionable|budget_trimmed_primary
assessment_reasons: list[str]
```

`AssessmentDirective` is machine-readable. Stage 7 may render a concise human
header from it, but should not reinterpret the packet.

---

## Progressive Disclosure

For implementation and debugging tasks, the default first emission is:

1. current success path
2. prerequisites or decisions required to apply it
3. major cautions and known failure paths
4. deferred branch summary, if useful

Do not emit full history simply because the reservoir contains it. Prefer
`followup_query_policy = on_first_failure` when deferred branches are plausible
but no concrete error signature has appeared yet.

Use `full_chain` only when the caller asks for history/process reconstruction or
when the chain itself is the answer.

---

## Fresh Pipeline Thresholds

Stage 6 does not run Stages 2-5. It may tell the caller or orchestrator that a
fresh run is justified only when one threshold is met:

| Threshold | Meaning |
|-----------|---------|
| `no_anchor` | No primary or reservoir entry matches query anchors. |
| `missing_required_role` | Required role remains missing after trace completion. |
| `new_error_signature` | Caller supplies a concrete error/log/test failure not represented in the reservoir. |
| `contradiction_blocks_use` | No safe branch can be selected due to contradictions. |
| `archive_only_actionable` | Only raw archive evidence exists for an action that needs canonical guidance. |
| `budget_trimmed_primary` | Budget pressure removed references needed for assessment. |

Otherwise Stage 6 should select from the existing reservoir and avoid asking the
retrieval path to repeat work.

---

## Non-Negotiable Invariants

- Stage 6 is pure computation: no DB access and no canonical writes.
- It must not call an LLM to classify sufficiency or completeness.
- `retrieval_state` and `completion_state` are separate outputs.
- The quality gate applies to every primary memory.
- Archive-only packets are not no-hit packets.
- Contradiction-blocked packets must not be recommended for direct reuse.
- Stage 7 must be able to follow the directive without re-assessing the packet.

---

## Completion Gate

Stage 6 is complete when:

1. `AssessedPacket` and `AssessmentDirective` are documented and implemented.
2. Runtime and shell/API assessment paths share one classifier.
3. Retrieval state covers all compatibility states.
4. Completion state covers complete, success-only, partial, fragmentary, and
   overabundant packets.
5. Reservoir branch selection is deterministic and tested.
6. Fresh-pipeline thresholds are deterministic and tested.
7. Stage 7 fixtures can emit from the directive without reading Stage 5 internals.

---

## Reference

**Roadmap:** [retrieval-assessment-roadmap.md](retrieval-assessment-roadmap.md)  
**Stage 5 input:** [retrieval-assembly-plan.md](retrieval-assembly-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)

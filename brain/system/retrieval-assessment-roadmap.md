# Retrieval Assessment Roadmap — Stage 6

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| S6-P1 | `AssessedPacket` and `AssessmentDirective` contract | done |
| S6-P2 | Trustworthiness classifier and compatibility `retrieval_state` | done |
| S6-P3 | Functional completeness classifier over path/reservoir metadata | done |
| S6-P4 | Progressive disclosure branch selection and hydration policy | done |
| S6-P5 | Fresh Stage 2-5 threshold policy | done |
| S6-P6 | Runtime/shell/API assessment path consolidation | done |
| S6-P7 | Metrics, degradation behavior, and Stage 7 handoff fixtures | partial |

---

## Design Mark

**2026-05-14:** Stage 6 architecture planned after Stage 5 bounded trace
completion and assessment reservoir design.

Design targets:

- separate trustworthiness from functional completeness
- retain existing `retrieval_state` compatibility
- add `completion_state`
- emit `AssessmentDirective` for Stage 7
- select reservoir branches for progressive disclosure
- prefer `on_first_failure` follow-up over fresh retrieval when reservoir
  branches already exist
- gate fresh Stage 2-5 reruns behind explicit thresholds
- keep Stage 6 pure, deterministic, no DB access, and no LLM calls

Implementation mark:

- `src/second_brain/assessor.py` owns hit/payload compatibility assessment and
  packet-level `AssessmentDirective` generation.
- `AssessmentDirective` and `AssessedPacket` are modeled in
  `src/second_brain/models.py`.
- `resolve_task_context()` now attaches `assessment_directive` and uses it for
  compatibility top-level `retrieval_state`, `recommendation`, and
  `verification_action`.
- Trustworthiness and completeness are separate classifier paths.
- Reservoir branch selection, deferred branches, hydration policy, follow-up
  policy, and fresh-pipeline threshold reason codes are emitted.
- Runtime and payload compatibility paths share the Stage 6 hit classifier.

Remaining:

- Runtime packet metrics still record only legacy retrieval/recommendation
  fields; directive-specific metrics remain for S6-P7 follow-up.
- Stage 7 has not yet consumed the directive because Stage 7 implementation is
  not started.

**2026-05-14 (Phase 8 cross-cut):** Gate-aware assessment added as part of
retrieval-polish. `_retrieval_state_for_packet` now reads
`assembly_notes.suppressed_by_gate_count` (populated by Stage 4 relevance gate
via `runtime._build_memory_packet`) and emits reason code
`relevance_gate_suppressed_all` instead of `no_primary_or_archive` when the
gate is the cause of no hits — letting agents distinguish a true corpus miss
from a relevance-filtered query. No new `AssessmentDirective` field added; the
reason code travels in the existing `retrieval_state` + assessment path.

---

## Implementation Notes

The first implementation should avoid broad refactors. The current assessment
logic exists in `runtime.assess_retrieval()` / `assess_retrieval_payload()`;
Stage 6 should extract that behavior into a shared assessor boundary and then
extend it to packet-level assessment.

Suggested target types:

```text
AssessedPacket:
  packet: MemoryPacket
  directive: AssessmentDirective

AssessmentDirective:
  retrieval_state
  completion_state
  reuse_mode
  recommendation
  verification_action
  emission_profile
  hydration_policy
  selected_branch_ids
  deferred_branch_ids
  missing_roles
  followup_query_policy
  fresh_pipeline_threshold
  assessment_reasons
```

---

## Completion Gate

Stage 6 is complete when:

1. Runtime, shell/API, and tests use the same assessment classifier.
2. Compatibility retrieval states match current behavior where appropriate.
3. Quality gate is applied across all primary memories.
4. Historical packets return `reuse_with_verification`.
5. Complete, success-only, partial-chain, fragmentary, and overabundant fixtures
   produce distinct `completion_state` values.
6. Reservoir branch selection is deterministic.
7. Fresh-pipeline thresholds are emitted only for documented reasons.
8. Stage 7 can render from `AssessmentDirective` without recomputing assessment.

Current verification:

- `python3 -m unittest tests.test_assessor tests.test_runtime_protocol` → 19 passed
- `python3 -m unittest tests.test_assessor tests.test_runtime_protocol tests.test_query_parser tests.test_reranker tests.test_recall tests.test_phase4` → 63 passed
- `python3 -m unittest tests.test_orchestrator.TestBeginTask tests.test_orchestrator.TestMarkFailure tests.test_orchestrator.TestFinishTask tests.test_orchestrator.TestFullOrchestrationLoop tests.test_orchestrator.TestContextMarkdownFormat` → 28 passed
- `python3 -m unittest tests.test_orchestrator.TestHTTPServer` → 11 passed
- `python3 -m unittest tests.test_orchestrator` → 64 passed

---

## Reference

**Detailed phase plans:**  
- [Phase 1](retrieval-assessment-plan-phase1.md) · [Phase 2](retrieval-assessment-plan-phase2.md) · [Phase 3](retrieval-assessment-plan-phase3.md)
- [Phase 4](retrieval-assessment-plan-phase4.md) · [Phase 5](retrieval-assessment-plan-phase5.md) · [Phase 6](retrieval-assessment-plan-phase6.md)
- [Phase 7](retrieval-assessment-plan-phase7.md)

**Governing plan:** [retrieval-assessment-plan.md](retrieval-assessment-plan.md)  
**Stage 5 input:** [retrieval-assembly-plan.md](retrieval-assembly-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)

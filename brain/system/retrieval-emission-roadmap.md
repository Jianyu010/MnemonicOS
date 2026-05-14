# Retrieval Emission Roadmap — Stage 7

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| S7-P1 | Emission contract, render model, and output profiles | not started |
| S7-P2 | Canonical JSON renderer and direct/runtime parity | not started |
| S7-P3 | Markdown renderer and full selected-packet presentation | not started |
| S7-P4 | Archive, warning, contradiction, and missing-role presentation | not started |
| S7-P5 | Directive-guided branch filtering and deferred context summary | not started |
| S7-P6 | Directive-specific metrics and retrieval trace emission | not started |
| S7-P7 | Shell/API consolidation and no-raise degraded output | not started |

---

## Implementation Mark

**2026-05-14 (Phase 8 — pre-Stage 7 polish):** 2D score triple, gate-suppressed
messages, and confidence band labels implemented as retrieval-polish cross-cuts
outside the formal Stage 7 render model. Covered by Phase 8 plan:
[retrieval-emission-plan-phase8.md](retrieval-emission-plan-phase8.md).

Implemented:
- `retrieval.py` JSON: every hit object gains `relevance_score` and `quality_score`
- `retrieval.py` JSON: `suppressed_by_gate_count` and `passed_gate_count` in result
- `mnemonic_context.sh` Markdown: gate-suppressed message ("N candidates recalled
  but suppressed (relevance_score < 0.25)") vs corpus-miss message; `relevance=`
  `quality=` per hit line; `>50%` suppression rate advisory as HTML comment
- `cli.py` text: `[HIGH/MEDIUM/LOW]` confidence band per hit; `relevance=`
  `quality=` fields in output; gate suppression summary line
- `assessor.py`: `insufficient_no_hit` reason code `relevance_gate_suppressed_all`
  propagated from `assembly_notes.suppressed_by_gate_count`

These are additive changes — no existing JSON fields renamed or removed. The
formal Stage 7 render model (S7-P1 through S7-P7) remains "not started" and
will supersede these ad-hoc outputs when implemented.

---

## Design Mark

**2026-05-14:** Stage 7 planned after Stage 6 produced
`AssessmentDirective` and `AssessedPacket` contracts.

Design targets:

- keep emission lightweight and deterministic
- treat `AssessmentDirective` as authoritative
- derive JSON and Markdown from one render model
- expose `archive_context` separately from canonical wiki memory
- preserve selected/deferred branch decisions
- emit focused requery instructions when Stage 6 requests a caller-mediated
  second pass
- preserve stable anchors in suggested follow-up queries so the next pass can
  recover the intended relation path
- support caller/orchestrator automatic focused retry with strict retry budget
  and hard cutoff conditions
- return composed multi-pass output when automatic retry runs, preserving both
  Stage 7 emissions and marking the final pass
- render no-memory and warning-only states explicitly
- surface completion state, reuse mode, verification action, and follow-up
  policy
- move directive-specific runtime metrics into instrumentation without
  canonical wiki writes
- consolidate direct retrieval, runtime, shell, and HTTP output paths

Current state:

- Stage 6 emits `assessment_directive` through `ResolveTaskContextResult`.
- Runtime Markdown already renders a basic `## Memory Context` block with
  retrieval state, recommendation, verification action, warnings, and memory
  slots.
- Direct `RetrieveResult` JSON exposes hits and archive hits but does not expose
  Stage 6 directive fields.
- Stage 7 has not yet introduced a shared render model, branch filtering, or
  JSON/Markdown parity tests.
- Stage 7 has not yet modeled `immediate_focused_requery` as a first-class
  emitted handoff.
- Stage 7 has not yet defined the exact anchor extraction/rendering helper for
  suggested follow-up queries.
- Stage 7 has not yet defined retry metadata, no-new-ID comparison, or cutoff
  output for automatic focused retry.
- Stage 7 has not yet defined the composed multi-pass response schema.

---

## Completion Gate

Stage 7 is complete when:

1. `retrieval-emission-plan.md` and all phase plans are implemented or updated
   to reflect the final design.
2. A shared render model drives JSON and Markdown outputs.
3. `assessment_directive`, `archive_context`, warnings, contradictions,
   deferred branches, missing roles, and follow-up policy are visible in the
   appropriate output formats.
4. `immediate_focused_requery` and `on_first_failure` produce structured
   follow-up query instructions without internal recursive retrieval.
5. Suggested follow-up queries preserve stable anchors: original intent,
   missing role, note IDs, chain ID, branch ID, error signature, and relation
   target when available.
6. Automatic focused retry is represented as external orchestrator control flow,
   not Stage 7 recursion.
7. Retry budget, no-new-ID, repeated-query, and same-threshold cutoff fixtures
   terminate with a hard stop notice.
8. Automatic retry responses return both Stage 7 emissions, pass-level query
   IDs, retry summary, and `final_pass_index`.
9. Direct retrieval, runtime context, shell, and HTTP outputs are parity-tested.
10. Directive-specific metrics are recorded as instrumentation only.
11. No-memory, warning-only, archive-only, overabundant, and malformed packet
   fixtures render without raising.
12. End-to-end retrieval smoke tests cover Stage 1 through Stage 7.

---

## Reference

**Detailed phase plans:**  
- [Phase 1](retrieval-emission-plan-phase1.md) · [Phase 2](retrieval-emission-plan-phase2.md) · [Phase 3](retrieval-emission-plan-phase3.md)
- [Phase 4](retrieval-emission-plan-phase4.md) · [Phase 5](retrieval-emission-plan-phase5.md) · [Phase 6](retrieval-emission-plan-phase6.md)
- [Phase 7](retrieval-emission-plan-phase7.md)

**Governing plan:** [retrieval-emission-plan.md](retrieval-emission-plan.md)  
**Stage 6 input:** [retrieval-assessment-plan.md](retrieval-assessment-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)

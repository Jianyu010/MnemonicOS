# Retrieval Assembly Roadmap — Stage 5

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| S5-P1 | Packet contract and explicit `archive_context` slot | done |
| S5-P2 | Primary readout selection and quality/warning routing | done |
| S5-P3 | Same-episode context and chain path roles via `chain_id` / session lineage | done |
| S5-P4 | Related slots, functional path reconstruction, bounded trace completion, and assessment reservoir | done |
| S5-P5 | Cross-slot deduplication, stable slot ordering, and score-vs-functional order preservation | done |
| S5-P6 | Context budget enforcement and body references | done |
| S5-P7 | Contradictions, warnings, trace metadata, and Stage 6 handoff notes | done |

---

## Implementation Mark

**2026-05-14:** Stage 5 implemented for the runtime packet path.

Implemented:
- `MemoryPacket.archive_context`
- `MemoryPacket.contradictions`
- `MemoryPacket.functional_path`
- `MemoryPacket.assembly_notes`
- per-item `rank_order`, `assembly_role`, and `assembly_order`
- archive hit preservation through `RetrieveResult.archive_hits`
- archive-only packet notes
- contested-hit contradiction evidence
- cross-slot deduplication with provenance merge
- functional path ordering that keeps Stage 4 score order as metadata while
  arranging readout by explanatory order

Additional implementation mark:
- bounded trace completion is implemented as deterministic Stage 5 assembly
  logic, not a Stage 2 recall loop
- `TracePurpose` is derived from typed query/task signals without an LLM call
- trace admission uses relation allowlist, role usefulness, and anchor/branch
  relevance before caps
- `MemoryPacket.assessment_reservoir` carries branch metadata for Stage 6
- assembly notes include trace status, trace-added IDs, reservoir counts,
  default/deferred branch IDs, and missing roles

Design target:
- deterministic trace completion from admitted seeds only
- allowlisted relation families: episode, timeline, replacement, causal/debug,
  derivation, and typed workflow links
- default caps: depth 2, 8 added wiki items, 3 branches, 6 items per branch,
  2 superseded/caution branches, 0 archive additions
- assessment reservoir with default/deferred branches for Stage 6 progressive
  disclosure decisions

Verification:
- `python3 -m unittest tests.test_runtime_protocol` → 12 passed
- `python3 -m unittest tests.test_reranker tests.test_recall tests.test_phase4` → 15 passed
- `python3 -m unittest tests.test_orchestrator tests.test_query_parser` → 92 passed
- `python3 -m unittest tests.test_phase2 tests.test_extraction_layer tests.test_passive_feedback_e2e` → 14 passed
- `python3 -m unittest discover -s tests` → 420 passed

Verification note:
- The 2026-05-14 trace-pass update reran `python3 -m unittest
  tests.test_runtime_protocol` successfully with 12 tests.

---

## Legacy State

Current assembly behavior exists inside `runtime._build_memory_packet()` and
`runtime._load_same_episode_context()`, but the stage boundary is not yet clean.

| Gap | Legacy location | Target |
|-----|----------------|--------|
| typed assembly contract | `MemoryPacket` lacks explicit archive/contradiction lanes | Stage 5 packet contract |
| archive readout | archive hits filtered out of primary but not surfaced as own slot | `archive_context[]` |
| primary selection | bucket caps exist, quality routing is implicit | explicit primary readout policy |
| same episode | `chain_id` expansion exists after packet construction | planned slot contract and dedupe interaction |
| functional path | ranked hits stay primarily score-ordered even when attempts/failures/successes imply a causal path | explicit `functional_path` / assembly order |
| related slots | relation bucketing exists, but no complete coherence pass | related-slot phase with ordering and path rules |
| trace completion | direct hit + one-layer expansion can leave process chains fragmentary | bounded relation-only trace pass inside Stage 5 |
| assessment reservoir | assembled packet is close to emitted packet, so overabundant chains are hard to defer cleanly | temporary branch reservoir consumed by Stage 6 |
| deduplication | partial prevention while selecting slots | explicit cross-slot dedupe pass |
| budget | slot caps exist, body references carried without full hydration | metadata- and reference-preserving context budget |
| assessment handoff | warnings are prose strings | structured assembly notes, contradiction evidence, trace status, and reservoir metadata |

---

## Completion Gate

Stage 5 is complete when:

1. `RawPacket` or the chosen packet contract is documented and implemented.
2. Archive chunks can only appear in `archive_context[]`.
3. Primary, same-episode, related, archive, warnings, contradictions, and
   assembly notes are independently testable.
4. Cross-slot deduplication is deterministic and tested against duplicate graph,
   semantic, and episode routes.
5. Success/failure path reconstruction is tested against scattered incidents,
   decisions, failed attempts, and successful procedures.
6. Bounded trace completion is tested against fragmentary chains, cap pressure,
   superseded branches, and overabundant branches.
7. The assessment reservoir exposes default and deferred branches without
   forcing Stage 7 to emit the whole chain.
8. Context budget enforcement is tested for metadata preservation.
9. Stage 6 can classify sufficiency, completeness, and emission profile from
   the assembled packet without DB access.

---

## Reference

**Detailed phase plans:**  
- [Phase 1](retrieval-assembly-plan-phase1.md) · [Phase 2](retrieval-assembly-plan-phase2.md) · [Phase 3](retrieval-assembly-plan-phase3.md)
- [Phase 4](retrieval-assembly-plan-phase4.md) · [Phase 5](retrieval-assembly-plan-phase5.md) · [Phase 6](retrieval-assembly-plan-phase6.md)
- [Phase 7](retrieval-assembly-plan-phase7.md)

**Governing plan:** [retrieval-assembly-plan.md](retrieval-assembly-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)

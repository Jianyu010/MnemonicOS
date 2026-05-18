# Ingestion Path Governance Design

## Purpose

Single reference for MnemonicOS ingestion architecture. Maps every layer from
raw intake to wiki maintenance, links each layer's governing plan and roadmap,
and defines what each layer owns and does not own.

---

## Ingestion Path Diagram

```
External Material (PDF, DOCX, agent episodes, attachments)
        │
        │  [bridge hooks, mnemonic ingest-artifact, ingest_session]
        ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 0 · Capture                                  │
│  ingestion-capture-plan.md                          │
│  Software-enforced raw capture from bridges.        │
│  Appends raw events to MnemonicOS via middleware.   │
└────────────────────┬────────────────────────────────┘
                     │ raw events / files
                     ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 1 · West Flange  (intake boundary)           │
│  ingestion-west-flange-plan.md                      │
│  ingestion-west-flange-roadmap.md                   │
│  Accepts true-raw, archives to brain/raw/,          │
│  projects RAM-only usable-raw (ProjectionLease,     │
│  TextWindow, ArtifactView). Enqueues extraction.    │
└────────────────────┬────────────────────────────────┘
                     │ ProjectionLease → extraction queue
                     ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 2 · Extraction  (LLM/VLM layer)              │
│  ingestion-extraction-plan.md                       │
│  ingestion-extraction-roadmap.md                    │
│  Consumes projections, runs VLM visual snippets +   │
│  LLM candidate bundles, validates, writes           │
│  promotion_ready rows to extraction_candidates.     │
│  Does NOT write wiki.                               │
└────────────────────┬────────────────────────────────┘
                     │ promotion_ready candidates
                     ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 3 · Promotion                                │
│  ingestion-promotion-plan.md                        │
│  ingestion-promotion-roadmap.md                     │
│  Deterministic: gate → decision → render →          │
│  atomic wiki write. Routes low-confidence /         │
│  conflicting candidates to brain/wiki/review/.      │
│  Writes promotion_decisions ledger.                 │
└──────┬──────────────────────────┬───────────────────┘
       │ promoted notes on disk   │ review_routed candidates
       │                          ▼
       │           ┌──────────────────────────────────┐
       │           │  AUTO-RESOLVE DAEMON             │
       │           │  wiki_auto_resolve_review        │
       │           │  (*/10 * * * *)                  │
       │           │  LLM arbitration of review items.│
       │           │  High-confidence → approve/reject│
       │           │  automatically. Low-confidence → │
       │           │  enrich file for human reviewer. │
       │           └──────────────┬───────────────────┘
       │                          │ human-fallback items only
       ▼                          ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 4 · Maintenance                              │
│  ingestion-maintenance-plan.md                      │
│  ingestion-maintenance-roadmap.md                   │
│  Operates on full wiki graph. Rebuilds relation     │
│  graphs, audits lifecycle, computes importance,     │
│  cross-wiki linking (embedding neighbor), and       │
│  raw re-visitation as schemas mature.               │
└──────────────┬──────────────────────────────────────┘
               │                  │
               │ review items     │ canonical wiki
               ▼                  ▼
┌────────────────────┐   ┌────────────────────────────┐
│  SIDE PATH         │   │  RETRIEVAL PATH            │
│  Review Command    │   │  East Flange (Stage 1)     │
│  ingestion-review- │   │  RETRIEVAL-PATH-           │
│  plan.md           │   │  GOVERNANCE-DESIGN.md      │
│  Human triage of   │   │  Read-only. Query →        │
│  review-routed     │   │  ranked assembly →         │
│  notes via         │   │  bounded memory packet.    │
│  mnemonic review   │   │  7-stage pipeline.         │
└────────────────────┘   └────────────────────────────┘
```

### Cross-cutting concern

```
retrieval-control-signal-quality-roadmap.md
  ↳ Improves signal quality and retrieval performance across
    all layers (summary fields, sufficiency assessment, chain_id).
```

---

## Layer Descriptions

### Layer 0 — Capture
**Plan:** [ingestion-capture-plan.md](ingestion-capture-plan.md)

Software-enforced raw capture by bridge middleware or platform hooks. Bridges
(claude, codex) should not rely on LLM-discretionary skill calls for writeback.
Capture is append-only at the event level. MnemonicOS owns file layout and
manifest. Feeds Layer 1 (West Flange).

---

### Layer 1 — West Flange
**Plan:** [ingestion-west-flange-plan.md](ingestion-west-flange-plan.md)
**Roadmap:** [ingestion-west-flange-roadmap.md](ingestion-west-flange-roadmap.md)

Raw-material intake boundary. Accepts externally supplied material, preserves
it as immutable true-raw under `brain/raw/`, and projects temporary usable-raw
(RAM-only) for downstream extraction. Key invariant: true-raw is never mutated
after archive. Projection is always temporary; it is not persisted. Phase 6
auto-enqueues extraction on ingest.

Phase files:
[P1](ingestion-west-flange-plan-phase1.md) ·
[P2](ingestion-west-flange-plan-phase2.md) ·
[P3](ingestion-west-flange-plan-phase3.md) ·
[P4](ingestion-west-flange-plan-phase4.md) ·
[P5](ingestion-west-flange-plan-phase5.md) ·
[P6](ingestion-west-flange-plan-phase6.md)

---

### Layer 2 — Extraction (LLM/VLM)
**Plan:** [ingestion-extraction-plan.md](ingestion-extraction-plan.md)
**Roadmap:** [ingestion-extraction-roadmap.md](ingestion-extraction-roadmap.md)

Consumes `ProjectionLease` + `TextWindow` + `ArtifactView` from Layer 1.
Runs VLM visual snippet extraction (Phase 4) and LLM candidate bundle inference
(Phase 5). Validates candidates deterministically (Phase 6). Writes
`promotion_ready` rows to `extraction_candidates`. Terminates before any wiki
write. Phase 8 extracts intra-source relations stored in candidate rows.

Phase files:
[P1](ingestion-extraction-plan-phase1.md) ·
[P2](ingestion-extraction-plan-phase2.md) ·
[P3](ingestion-extraction-plan-phase3.md) ·
[P4](ingestion-extraction-plan-phase4.md) ·
[P5](ingestion-extraction-plan-phase5.md) ·
[P6](ingestion-extraction-plan-phase6.md) ·
[P7](ingestion-extraction-plan-phase7.md) ·
[P8](ingestion-extraction-plan-phase8.md)

---

### Layer 3 — Promotion
**Plan:** [ingestion-promotion-plan.md](ingestion-promotion-plan.md)
**Roadmap:** [ingestion-promotion-roadmap.md](ingestion-promotion-roadmap.md)

Deterministic software layer. Consumes `promotion_ready` candidates, runs gate
checks, makes decision (promote / hold / reject / review), renders markdown, and
performs atomic wiki write. Routes low-confidence / conflicting candidates to
`brain/wiki/review/`. Writes `promotion_decisions` ledger. Intra-source relations
amendment applies extracted relations to note frontmatter and graph edges.

The `wiki_auto_resolve_review` daemon then attempts LLM arbitration on routed
items; items it cannot resolve with sufficient confidence (< 0.75) are left
enriched with `## LLM Analysis` for human review.

Phase files:
[P1](ingestion-promotion-plan-phase1.md) ·
[P2](ingestion-promotion-plan-phase2.md) ·
[P3](ingestion-promotion-plan-phase3.md) ·
[P4](ingestion-promotion-plan-phase4.md) ·
[P5](ingestion-promotion-plan-phase5.md) ·
[P5-gaps](ingestion-promotion-plan-phase5-gaps.md) ·
[Intra-source relations](ingestion-promotion-plan-intra-source-relations.md)

---

### Layer 4 — Maintenance
**Plan:** [ingestion-maintenance-plan.md](ingestion-maintenance-plan.md)
**Roadmap:** [ingestion-maintenance-roadmap.md](ingestion-maintenance-roadmap.md)

Holistic wiki governance. Operates on the full wiki graph, not individual
candidates. Responsibilities (lightest to heaviest):
- Layer 1: Cross-wiki linking (per-ingestion + scheduled sweep)
- Layer 2: Lifecycle advancement (scheduled audit)
- Layer 3: Importance scoring (scheduled compute)
- Layer 4: Contradiction detection and review routing
- Layer 5: Raw re-visitation (re-extract as schemas mature)

Embedding model decision: [ingestion-maintenance-embedding-model.md](ingestion-maintenance-embedding-model.md)

Phase files:
[P1](ingestion-maintenance-plan-phase1.md) ·
[P2](ingestion-maintenance-plan-phase2.md) ·
[P3](ingestion-maintenance-plan-phase3.md) ·
[P4](ingestion-maintenance-plan-phase4.md) ·
[P5](ingestion-maintenance-plan-phase5.md) ·
[P6](ingestion-maintenance-plan-phase6.md) ·
[P7](ingestion-maintenance-plan-phase7.md) ·
[P8](ingestion-maintenance-plan-phase8.md)

---

### Retrieval Path — East Flange
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
**Roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)

Read-only output path. Accepts a natural-language query, runs the 7-stage
pipeline (query parsing → multi-channel recall → signal integration → reranking
→ assembly → assessment → emission), and returns a bounded memory packet.
Does not modify any stored content. Writes only to instrumentation tables as
side-channel signals.

"East flange" refers specifically to Stage 1 (Query Parsing) — the query intake
boundary. Stages 2–7 are internal retrieval components.

Stage plan files:
[Stage 1 — East Flange (Query Parsing)](retrieval-east-flange-plan.md) ·
[Stage 1 Roadmap](retrieval-east-flange-roadmap.md)
(stages 2–7 plans follow same naming: retrieval-recall-plan.md, etc.)

Audit baseline: [docs/east-flange-audit.md](../../docs/east-flange-audit.md)

---

### Side path — Auto-Resolve Daemon
**Spec:** [docs/superpowers/specs/2026-05-18-auto-resolve-review-design.md](../../docs/superpowers/specs/2026-05-18-auto-resolve-review-design.md)

LLM-arbitrated resolution of `review_routed` candidates. Runs every 10 minutes
(`wiki_auto_resolve_review`). Re-evaluates each item against current wiki state:
auto-promotes if decision changed, or dispatches to a reason-specific LLM resolver
(`low_confidence`, `type_collision`, `wiki_conflict`, `contested_lifecycle`).
When confidence ≥ 0.75, approves or rejects automatically. When confidence < 0.75
or action is `human`, appends `## LLM Analysis` to the review file so human
reviewers receive full context.

---

### Side path — Review Command
**Plan:** [ingestion-review-plan.md](ingestion-review-plan.md)
**Roadmap:** [ingestion-review-roadmap.md](ingestion-review-roadmap.md)

Ergonomic human triage for items in `brain/wiki/review/` that the auto-resolve
daemon could not resolve with confidence (≥ 0.75). The `mnemonic review` command
presents these enriched items (with `## LLM Analysis` attached) one at a time,
lets the operator approve (advance lifecycle, move to wiki) or reject (delete,
mark ledger). Reduces manual file editing to zero.

Phase files:
[P1](ingestion-review-plan-phase1.md) ·
[P2](ingestion-review-plan-phase2.md) ·
[P3](ingestion-review-plan-phase3.md)

---

### Cross-cutting — Control Signal Quality
**Roadmap:** [control-signal-quality-roadmap.md](control-signal-quality-roadmap.md)

Addresses signal quality failures that cause agents to loop despite having
relevant memory. Three problems: notes lack compact control signal (no gene
string), sufficiency assessment ignores content quality, episode notes lack
`chain_id` for cross-session arc loading. Fixes span Layers 2–5.

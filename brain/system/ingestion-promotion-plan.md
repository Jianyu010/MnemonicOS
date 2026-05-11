# Ingestion: Promotion Plan

## Purpose

This plan directly follows `brain/system/ingestion-extraction-plan.md`. The
LLM/VLM layer owns projection consumption, model inference, and candidate
validation. It terminates at `promotion_ready` rows in `extraction_candidates`.
This promotion layer starts at those rows: it decides how each candidate becomes
a canonical wiki note using deterministic software logic only.

This governing file defines scope, vocabulary, invariants, and phase order. The
implementation details live in the phase files:

1. [Phase 1: Promotion Gate](ingestion-promotion-plan-phase1.md)
2. [Phase 2: Decision Engine](ingestion-promotion-plan-phase2.md)
3. [Phase 3: Note Renderer](ingestion-promotion-plan-phase3.md)
4. [Phase 4: Wiki Writer](ingestion-promotion-plan-phase4.md)
5. [Phase 5: Daemon and Admin Surfaces](ingestion-promotion-plan-phase5.md)
6. [Amendment: Intra-Source Relation Application](ingestion-promotion-plan-intra-source-relations.md)

## Verified Breakpoint

Current state at start of this layer:

- `tests.test_extraction_layer` passes.
- `tests.test_west_flange` passes.
- `tests.test_session_capture` passes.
- Full repository test suite passes: 198 tests.
- `extraction_candidates` table exists with `promotion_state` and
  `validation_status` columns.
- `promotion_ready` candidates are produced by extraction worker but not yet
  consumed by any wiki-writing layer.
- `brain/system/promotion-layer-plan.md` is a stub with no decision rules.

## Glossary

- **Promotion layer**: the MnemonicOS layer that converts `promotion_ready`
  extraction candidates into canonical wiki notes using deterministic logic.
- **Promotion gate**: the ordered set of software checks that must all pass
  before any wiki write is allowed for a candidate.
- **Decision engine**: the deterministic rule set that assigns one of four
  outcomes to each candidate that passes the gate: `new`, `confirm`, `update`,
  or `review`.
- **Note renderer**: the module that converts a candidate payload (mnemos-extraction-v1
  schema) into a canonical `NoteRecord` markdown document.
- **Wiki writer**: the module that performs atomic file writes to `brain/wiki/`
  and records promotion decisions in the ledger.
- **Promotion ledger**: the `promotion_decisions` SQLite table that records
  which candidate became which note, what decision was made, and when.
- **`new`**: candidate covers content not present in any existing wiki note —
  create a new note.
- **`confirm`**: candidate corroborates an existing note without adding new
  content — strengthen confidence or usefulness signals only.
- **`update`**: candidate adds a durable refinement to an existing note — merge
  content fields, update body, source refs, or confidence.
- **`review`**: candidate conflicts with existing notes, has low confidence, or
  triggers a contested lifecycle transition — route to `brain/wiki/review/` for
  human inspection.
- **`rejected`**: candidate failed the promotion gate — no wiki write; gate
  errors recorded in the ledger.
- **Atomic write**: write to a `.tmp` path, validate, then `os.rename()` to
  the final path. The rename is atomic on POSIX. Failure before rename leaves
  no partial state in wiki.
- **Idempotent**: re-running promotion on the same `candidate_id` produces the
  same outcome and does not create duplicate notes or ledger rows.
- **Source-grounded**: every claimed component, trace, or piece of evidence
  points back to a specific window ID, source locator, artifact marker, or
  artifact ID from the projection that produced the candidate.
- **Provenance**: each promoted note records the `candidate_id` and `archive_id`
  that produced it in its `source_refs` field.
- **NoteRecord**: the canonical in-memory model for a wiki note, defined in
  `src/second_brain/models.py`.
- **LIFECYCLE_DEFAULTS**: per-type default lifecycle states defined in
  `src/second_brain/ingest.py`.
- **TYPE_TO_DIRECTORY**: per-type target directory under `brain/wiki/` defined
  in `src/second_brain/ingest.py`.
- **config.promotion**: the `[promotion]` section of `config/memory.toml`.
- **mnemos-extraction-v1**: the JSON schema version produced by the LLM/VLM layer.
- **`duplicate_state`**: a label on each extraction queue row set by the west
  flange. `None` = novel source; `"exact_duplicate"` = byte-identical (west
  flange skips projection, so these never reach promotion); `"canonical_duplicate"`
  or `"lsh_duplicate"` = near-duplicate that the west flange passed through
  intentionally. The promotion layer treats the near-duplicate states as
  reinforcement priors: same content seen again from a different source is
  evidence the knowledge is reliable, not noise to be suppressed.
- **Knowledge maintenance layer**: a future MnemonicOS layer that operates on
  the full wiki after promotion, rebuilding link graphs, updating freshness and
  importance weights, and advancing lifecycle states based on cross-note signals.
  Distinct from the promotion layer, which writes individual notes from individual
  candidates.
- **Intra-source relation**: a typed relation asserted by the LLM between two
  candidates produced from the same true raw material (same extraction queue row /
  same bundle). Stored in `extraction_queue.intra_source_relations_json`. Applied
  to wiki note frontmatter by the promotion worker's bundle-aware second pass after
  all per-candidate writes in a run complete.
- **Bundle-aware second pass**: a post-candidate-loop step in `promotion/worker.py`
  that loads `intra_source_relations_json` for each queue row processed in the
  current run, resolves candidate titles to promoted note IDs, and writes typed
  relations to source note frontmatter. Triggers `rebuild_note_graph` for each
  updated note. Requires LLM/VLM Phase 8 to be implemented first.
- **`apply_relation_to_note`**: atomic frontmatter mutation function that appends
  a `{target, type}` entry to the `relations` list in a note's YAML frontmatter,
  deduplicates, and writes back via `.tmp` → `os.rename()`.

## Boundary

Inputs:

- `extraction_candidates` rows with `promotion_state = 'promotion_ready'`
- `payload_json` stored in each candidate row
- evidence IDs reconstructed from `extraction_visual_snippets` and candidate payload
- config values from `config.promotion`
- existing wiki state in `brain/wiki/`

Outputs:

- canonical markdown notes in `brain/wiki/`
- `promotion_decisions` ledger rows
- updated `extraction_candidates.promotion_state` field
- entries in `brain/wiki/log.md`

Out of scope:

- model inference (done by LLM/VLM layer)
- first-pass candidate structural validation (done by `validation.py`)
- retrieval (done by east flange)
- raw intake and projection (done by west flange)
- north-flange task context resolution
- reinforcement learning or fine-tuning pipelines

## Non-Negotiable Invariants

- Deterministic: same candidate input + same existing wiki state → same
  promotion decision. No LLM calls at this layer.
- Gate-first: no wiki write until all promotion gate checks pass.
- Schema-validated: every promoted note must parse cleanly with `parser.py`
  before the atomic rename commits it to wiki.
- Provenance-preserving: each promoted note must link back to the candidate row
  and the source archive ID.
- Idempotent: re-running promotion on the same `candidate_id` must not create
  duplicate notes or ledger rows.
- Rollback-safe: promotion failures must not leave partial note state in wiki.
- Low-confidence guard: candidates with `confidence = "low"` always route to
  `review`; they never receive a direct `new`, `confirm`, or `update` decision.
- No per-source count cap: a single source may produce any number of candidates.
  Quality is enforced per-candidate by the gate, not by limiting volume.
- Near-duplicate reinforcement: candidates from sources that the west flange
  passed through with a `canonical_duplicate` or `lsh_duplicate` label are
  treated as corroboration signals and biased toward `confirm` when a matching
  note exists.

## High-Level Flow

```text
extraction_candidates (promotion_state = 'promotion_ready')
    |
    v
Phase 1: promotion gate — 7 deterministic checks
    | fail: mark rejected, write ledger, stop
    | pass
    v
Phase 2: decision engine — new | confirm | update | review
    |
    v
Phase 3: note renderer — candidate payload → NoteRecord markdown
    |
    v
Phase 4: wiki writer — atomic write + ledger + log.md
    |
    v
Phase 5: daemon + admin surfaces — schedule, CLI, HTTP endpoint
    |
    v
Amendment: intra-source relation application — bundle-aware second pass
    resolve title → note_id, write relations to frontmatter, rebuild graph
    |
    v
sync + index materializers (embed_backfill, FTS sync)
```

## Phase Dependencies

- Phase 1 can be implemented and tested independently with fixture candidates.
- Phase 2 depends on Phase 1 gate passing and calls `bridge.decide_upsert()`.
- Phase 3 depends on Phase 2 decision and produces rendered markdown for
  a specific decision type.
- Phase 4 depends on Phases 1–3 and requires the `promotion_decisions` migration.
- Phase 5 depends on Phase 4 and wires the full pipeline into the daemon
  schedule, CLI, and HTTP admin surface.

## Out Of Scope

- Running LLMs or VLMs inside the promotion layer.
- Changing the candidate validation rules (those belong in extraction Phase 6).
- Retrieval or search over `brain/wiki/`.
- A UI for reviewing routed items (human review of `brain/wiki/review/` is manual).
- HTTP transport for promotion input; promotion always reads from SQLite directly.
- Knowledge maintenance: cross-note link rebuilding, freshness and importance
  weight updates, lifecycle-state advancement based on wiki-wide signals. These
  belong in a separate knowledge maintenance layer that runs against the full wiki
  after promotion has written notes. The existing `daily_consolidation` and
  `weekly_hygiene` daemon jobs are the current precursors to that layer. The
  maintenance layer will be software-driven initially, with an extensible seam for
  AI-assisted relationship discovery added later.

## Readiness

The design is ready to implement once the five phase files are treated as the
source of truth. Each phase contains concrete modules, schema changes, algorithms,
API shapes, and verification requirements.

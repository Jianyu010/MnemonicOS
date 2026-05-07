# East Flange Plan

## Status

Current retrieval is implemented and operational. The future learned retrieval
core is an architectural vision — not yet designed or built.

---

## Current State

The east flange today is `retrieval.py` + `trust.py` + `graph.py`, delivering
hybrid retrieval over canonical wiki notes and the SQLite/vector/graph substrate.

Retrieval channels:

- exact match
- BM25 full-text search
- semantic (vector) search
- graph traversal (relations, chain context)
- archive fallback (raw sessions when wiki misses)

Output: bounded `memory_packet` with `retrieval_state`, `recommendation`,
`verification_action`, `primary_memories`, `same_episode_context`, warnings,
and provenance. See `runtime-protocol.md` for the full packet schema.

---

## Future: Learned Retrieval Core

The long-term vision is a read-only retrieval model that:

- takes a natural-language inquiry as input
- fetches and sorts relevant canonical wiki notes and context
- returns a ranked, bounded memory packet
- does **not** modify any wiki content, raw archives, or index state
- is trained/fine-tuned for MnemonicOS-specific retrieval quality

This model would replace or augment the current heuristic hybrid stack. It
requires:

- evaluation data from real retrieval hits and misses (see `evals/candidates.jsonl`)
- a training signal from outcome feedback (useful/misleading memory IDs recorded
  per task)
- a well-defined canonical note schema that the model can reason over

---

## Boundary Rules (Both Current And Future)

- east flange is read-only: it never writes wiki, never mutates raw archives,
  never triggers promotion
- it may update retrieval hit logs and trust scores in SQLite (side-channel
  signal, not canonical state)
- retrieval packets must not expose true raw or usable raw projections as
  canonical memory
- archive fallback results must be clearly distinguished from canonical wiki hits

---

## What Is Not Part Of This Layer

- ingestion (west flange)
- extraction and promotion (LLM/VLM + promotion layers)
- north-facing agent APIs (orchestrator, bridge hooks)
- storage backend choices (south/substrate boundary)

---

## Next Step

The immediate retrieval work is in
`development/roadmap-control-signal-quality-and-retrieval-performance.md`
(gene strings, content quality gate, chain_id machinery). That work is ongoing.

The learned retrieval model is a future design task. No plan exists yet.

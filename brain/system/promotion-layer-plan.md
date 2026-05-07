# Promotion Layer Plan

## Status

Not yet implemented. This is a stub for the next major development layer.

---

## Position In Pipeline

```
extraction_candidates (promotion_ready rows)
    ↓
[this layer]
    ↓
brain/wiki/* canonical notes
    ↓
sync + SQLite/index materializers
```

The promotion layer sits between the LLM/VLM extraction layer and the canonical
wiki. It consumes `promotion_ready` candidates from `extraction_candidates` and
decides how they become canonical notes.

---

## Responsibilities

- consume `promotion_ready` candidate rows from `extraction_candidates`
- for each candidate, decide one of: create, update, confirm, review
- render candidate payloads into canonical markdown notes using note schemas
- write to `brain/wiki/` following governed lifecycle rules
- record promotion decisions (which candidate became which note, why)
- trigger sync and index materializers after writes
- never write wiki directly from the LLM/VLM extraction layer

---

## Non-Negotiable Invariants

- deterministic: same candidate input + same existing wiki state → same
  promotion decision, no LLM discretion at this layer
- schema-validated: every promoted note must conform to the relevant note-family
  schema before write
- provenance-preserving: each promoted note must link back to the candidate row
  and the source archive ID
- idempotent: re-running promotion on the same candidate must not create
  duplicate notes
- rollback-safe: promotion failures must not leave partial note state in wiki

---

## Decision Rules (To Be Specified)

The following decision vocabulary is inherited from the runtime protocol:

- `confirm`: candidate matches an existing note — strengthen confidence/usefulness
  signals, do not rewrite core content
- `update`: candidate adds durable refinement to an existing note
- `new`: candidate covers genuinely missing content — create a new note
- `review`: candidate conflicts with existing notes or has low confidence —
  route to `brain/wiki/review/` for human inspection

Rules for choosing among these are not yet written. The promotion layer plan
should specify them before implementation starts.

---

## What Is Not Part Of This Layer

- model inference (done by LLM/VLM layer)
- candidate validation (done by `validation.py` in extraction layer)
- retrieval (done by east flange)
- raw intake (done by west flange)

---

## Next Step

Write the full governing plan for this layer before implementation starts.
The plan should cover: decision rule specification, note-family normalization
from candidate payload to canonical schema, promotion record bookkeeping,
conflict handling, and rollback behavior.

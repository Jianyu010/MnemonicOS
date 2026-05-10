# Episode-Aware Retrieval

## Purpose

Record the status of episode-aware selective promotion and retrieval work,
and the gate conditions for future phases.

---

## What Is Done (Phase 1)

Optional episode metadata is fully implemented and live:

- `chain_id`, `chain_confidence`, `chain_question`, `chain_summary`,
  `problem_class`, `failure_summary`, `attempt_summaries`, `solution_rationale`
  are supported fields on canonical notes.
- Notes without chain metadata parse and ingest identically to before.
- Malformed episode metadata degrades cleanly to flat note handling.
- `chain_id` is available on `task_outcomes` for episode-aware retrieval
  grouping.
- Same-episode context (`same_episode_context` in the retrieval packet) fires
  only after a strong primary hit and is bounded.

---

## What Is Deferred (Phases 2–6)

The following phases are intentionally not started. They require evaluation
data that does not yet exist.

| Phase | Description | Gate condition |
|-------|-------------|----------------|
| 2 | Confidence-based chain extraction at promotion time | Evaluation shows inferred chains improve retrieval |
| 3 | Per-episode durable promotion cap | Note growth data from Phase 1 shows over-promotion |
| 4 | Trace-compatible graph relations | Phase 2 chain structure is stable |
| 5 | Episode-aware retrieval ranking | Phase 4 relations are in place |
| 6 | Evaluation before broad rollout | This is the gate itself |

Do not implement Phases 2–6 before the evaluation criteria in Phase 6 are
reviewed and the data exists to measure them.

---

## Future First-Class Trace Note

If evaluation shows strong value, a first-class `trace` or `case` note kind
can be added later. It should:

- reuse `chain_id` as the canonical trace identifier
- preserve all existing promoted notes and links
- replace synthetic same-episode bundling with direct trace-note retrieval

This is intended but explicitly not part of the current roadmap.

---

## Governing Reference

The full implementation plan with acceptance criteria is in
`development/roadmap-episode-aware-selective-promotion.md`.

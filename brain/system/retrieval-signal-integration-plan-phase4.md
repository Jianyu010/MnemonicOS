# Retrieval Signal Integration Phase 4: Passive Feedback Evidence Model

## Goal

Define the evidence vocabulary that lets retrieval learn from sessions even when
the agent does not explicitly report `useful_id` or `misleading_id`.

## Scope

- passive feedback labels, confidence levels, and materialized table ownership
- broad LLM-mediated evidence definitions
- ownership split between retrieval, runtime, promotion, and maintenance

## Evidence Levels

| Label | Meaning | Strength |
|-------|---------|----------|
| `hit` | candidate entered recall/ranking pool | weak exposure |
| `selected` | candidate was emitted into result packet | weak exposure |
| `passive_used_likely` | later raw/promoted session appears to rely on it | medium positive |
| `passive_useful_likely` | task trajectory suggests it helped | medium positive |
| `passive_misleading_likely` | later evidence suggests it misled or was stale | medium negative |
| `explicit_used` | caller/outcome named it useful | strong positive |
| `explicit_misleading` | caller/outcome named it misleading | strong negative |

## Broad Evidence Sources Upstream

Evidence is not limited to exact semantic matches. Promotion and maintenance may
use LLM/VLM power to infer attribution from:

- explicit note IDs, titles, aliases, or quotes
- paraphrased guidance or concepts from retrieved summaries/bodies
- code edits, commands, decisions, or plans aligned with retrieved guidance
- later corrections, reversals, or "that memory was stale/wrong" statements
- fresh research or new sources that contradict or supersede a retrieved note
- promoted incidents linked to retrieved procedures/decisions
- task success/failure trajectory combined with retrieved note usage
- repeated retries that avoid or replace prior retrieved guidance

Extraction now has a dedicated inline attribution shape:

```json
"retrieval_attribution": [
  {"note_id": "procedure/example", "evidence_type": "semantic_reliance", "confidence": 0.8}
]
```

Valid `evidence_type` values are `paraphrase`, `semantic_reliance`,
`task_trajectory`, `correction`, `contradiction`, and `supersession`.
Extraction may only reference note IDs present in the session's `## Memory
Context` block.

## Two Passive Paths

- `extraction_inline`: maintenance converts
  `promotion_decisions.retrieval_attribution_json` into
  `note_passive_feedback(method='extraction_inline')`. This path can emit only
  `passive_used_likely` because the current extraction window cannot observe
  later task outcome.
- `maintenance_post_hoc`: maintenance joins retrieval logs with promoted session
  evidence and may use an LLM attribution pass. This path may emit
  `passive_used_likely`, `passive_useful_likely`, or
  `passive_misleading_likely`.

## Ownership

- Stage 3/4 logs exposure and carries feature context.
- Runtime records explicit `useful_ids` / `misleading_ids` when available.
- Extraction emits optional `retrieval_attribution` in candidate payloads.
- Promotion stores attribution evidence in `promotion_decisions`; it does not
  write passive feedback rows.
- Maintenance is the sole writer of `note_passive_feedback`, updates
  trust/freshness, and feeds rerank training.
- Stage 3 reads `note_passive_feedback` after maintenance materializes it.

## Guardrails

1. Do not infer `misleading` from task failure alone.
2. Require note-specific attribution before applying strong negative labels.
3. Keep passive labels confidence-weighted and weaker than explicit labels.
4. Preserve evidence provenance so review can audit why a note was demoted.

## Verification

- passive labels are represented separately from explicit labels
- weak exposure cannot become usefulness by itself
- maintenance can consume the vocabulary without Stage 3 reading raw sessions
- Stage 3 can load materialized `note_passive_feedback` without reading
  `promotion_decisions` or extraction payloads

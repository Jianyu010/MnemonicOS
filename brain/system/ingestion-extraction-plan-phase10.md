# Extraction Layer Phase 10: Confidence Signal Clarification and Context Injection Removal

## Goal

Make extraction-layer `confidence` a well-defined, auditable signal grounded solely
in source evidence quality. Remove `existing_knowledge_context` from the extraction
pipeline entirely. Strengthen intra-session temporal supersession guidance so the
extraction LLM correctly handles self-corrections within a single session.

---

## Background and Motivation

### The `confidence` signal is currently undefined

The extraction prompt instructs the LLM that `confidence MUST be one of: "low",
"medium", "high"` with no criteria for what each level means. The LLM uses its own
pre-training intuitions, producing inconsistent and model-dependent results:
- Different models assign different distributions to the same session content.
- The LLM may conflate "weakly evidenced" with "already covered by wiki" — two
  different concepts that belong to different layers.
- No auditable rule exists to explain why a candidate received a given level.

### `existing_knowledge_context` contaminates the wrong layer

`existing_knowledge_context` is injected into revisit extraction packets
(`make_llm_packet` in `providers.py`). It contains wiki notes retrieved by
`retrieve_fn(source_ref)` at revisit-queue time. The intent was to help the LLM
avoid re-extracting already-known facts.

This is the wrong layer for that decision:
- The extraction LLM's job is to faithfully read the raw source. Wiki comparison
  is the promotion layer's job.
- Injecting old wiki state makes the LLM second-guess evidence quality against
  "what we already know" — contaminating the `confidence` signal.
- For fresh ingestion, no such context exists, causing inconsistent LLM behaviour
  between fresh and revisit extractions on identical source content.

Cross-wiki comparison and "already known" deduplication already happen in
`decide()` via `find_existing_candidates`. The extraction layer does not need to
replicate this.

### Intra-session temporal supersession is unguided

A session may contain self-corrections: the agent checks a file, makes a wrong
claim, the user corrects it, the agent checks more sources and makes a correct
claim. Both statements appear in the same windows. The extraction LLM must:
1. Recognise that statement B comes after A and is better evidenced.
2. Produce only B as the candidate (or B with a `supersedes` intra-source relation
   to A's component).
3. Assign B's confidence based on the quality of its evidence — not A's.

Currently the prompt has no guidance for this pattern.

---

## Changes

### 1. Remove `existing_knowledge_context` from extraction

**`src/second_brain/extraction/providers.py`** — `make_llm_packet()`:
- Remove `existing_knowledge_context` parameter.
- Remove the `if existing_knowledge_context: packet["existing_knowledge_context"] = ...` block.

**`src/second_brain/extraction/worker.py`** — `_process_item()`:
- Remove `existing_knowledge_context = _existing_knowledge_context(item.revisit_context_json)`.
- Remove `existing_knowledge_context=existing_knowledge_context` from `make_llm_packet(...)`.

**`src/second_brain/maintenance.py`**:
- Remove `requeue_context_enabled` and `requeue_context_top_k` from
  `MaintenanceConfig`.
- Remove `context_payload` construction and `retrieve_fn` call in
  `run_raw_revisitation`.
- Remove `_revisit_context_payload` helper.
- `enqueue_revisit_extraction`: set `context=None` always; simplify signature.

**`config/memory.example.toml`**:
- Remove `requeue_context_enabled` and `requeue_context_top_k` from
  `[maintenance]`.

The `revisit_context_json` column stays in the DB schema (historical data), but
nothing writes it going forward.

### 2. Add explicit confidence level definitions to the extraction prompt

In `_candidate_extraction_prompt()` in `providers.py`, replace:

```
- confidence MUST be one of: "low", "medium", "high"
```

with:

```
- confidence MUST be one of: "low", "medium", "high"
  - "high": the claim is directly and explicitly stated in the source windows;
    the evidence quote alone would convince a sceptical reader
  - "medium": the claim requires inference or synthesis across multiple windows,
    or was initially stated but later corrected within this session
  - "low": the claim is speculative, ambiguous, weakly evidenced, or the source
    itself expresses uncertainty about it
  NOTE: confidence measures source evidence quality only. Do NOT factor in
  whether the claim seems "already known" or "novel" — that is not your concern.
```

### 3. Strengthen intra-session temporal supersession guidance

In `_candidate_extraction_prompt()`, add after the CANDIDATE RULES section:

```
INTRA-SESSION CORRECTION RULES:
- Sessions may contain self-corrections: an early statement (A) followed by a
  later statement (B) that contradicts or refines A based on more evidence.
- When you detect this pattern: produce B as the candidate, not A.
- Assign B's confidence based on the quality of B's source evidence, ignoring A.
- If A and B are both extractable as distinct facts (not a simple correction),
  produce both and assert an intra-source relation: B supersedes A.
- Do not produce A alone when B clearly supersedes it within the same session.
```

---

## Config Changes

**`MaintenanceConfig`** in `src/second_brain/config.py`:
- Remove `requeue_context_enabled: bool`
- Remove `requeue_context_top_k: int`

---

## Scope

Modified:
- `src/second_brain/extraction/providers.py` — remove `existing_knowledge_context`
  from `make_llm_packet`; extend confidence definitions; add temporal supersession
  guidance
- `src/second_brain/extraction/worker.py` — remove context injection call
- `src/second_brain/maintenance.py` — remove `_revisit_context_payload`,
  `requeue_context_enabled`, `requeue_context_top_k`, context retrieval in
  `run_raw_revisitation`
- `src/second_brain/config.py` — remove two `MaintenanceConfig` fields
- `config/memory.example.toml` — remove two `[maintenance]` keys

No DB schema changes (leave `revisit_context_json` column as historical record).

---

## Acceptance Criteria

- `make_llm_packet()` has no `existing_knowledge_context` parameter.
- Fresh extraction and revisit extraction produce identical prompt structure for
  identical source content.
- Extraction prompt contains explicit `"high"` / `"medium"` / `"low"` definitions
  with `NOTE: confidence measures source evidence quality only`.
- Extraction prompt contains INTRA-SESSION CORRECTION RULES section.
- `MaintenanceConfig` has no `requeue_context_enabled` or `requeue_context_top_k`.
- `run_raw_revisitation` does not call `retrieve_fn` or build `context_payload`.
- All existing tests pass; fixture providers produce `"medium"` confidence for
  inferred candidates, `"high"` for directly stated claims.

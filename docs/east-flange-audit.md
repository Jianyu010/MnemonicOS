# East Flange Phase 0 Audit

## Purpose

Map the legacy east flange implementation against the 7-stage pipeline defined in
`brain/system/east-flange-development-plan.md`. Identify bugs, missing features,
boundary violations, and decide extend vs. redesign per module.

**Audit date:** 2026-05-11
**Auditor:** claude agent

---

## Legacy Module Map

| Module | Lines | Stage(s) served |
|--------|-------|----------------|
| `retrieval.py` | 650 | 1 (classification), 2 (all channels), 4 (scoring loop), 7 (logging) |
| `trust.py` | 481 | 3 (signal load), 4 (rerank weights + training), 6 (freshness multiplier) |
| `graph.py` | 262 | 2 (graph channel), maintenance side-channel |
| `runtime.py` | 806 | 5 (assembly), 6 (assessment), 7 (format + protocol wrapper) |
| `bridge.py` | ~400 | 6 (assessment payload path), writeback (west side) |
| `embedding_providers.py` | 200 | 2 (semantic channel) |

**Structural problem:** `retrieve()` in `retrieval.py` is a 350-line monolith that
implements Stages 1, 2, 3, 4, and 7 in a single function with no typed inter-stage
contracts. Stage boundaries do not exist as code boundaries.

---

## Stage-by-Stage Audit

### Stage 1 — Query Parsing

**Implemented in:** `retrieval._classify_query()` (lines 126–140), `trust.query_temporality()` (lines 72–79)

**What works:**
- Query type classification via keyword match over 6 type buckets
- Confidence score formula: `min(1.0, 0.45 + 0.2 × match_count)`
- `query_type_hint` override path

**Bugs / gaps:**
- `query_temporality` defaults to `"current"` for all ambiguous queries
  (line 78: `return "current"`) — `"mixed"` and `"unknown"` states defined in protocol
  but never returned
- Temporality computed independently by `retrieve()` AND again by `runtime.assess_retrieval()` —
  duplicated, can diverge
- No term expansion — BM25 queries hit only raw query tokens, no synonym or alias expansion
- No anchor term extraction — exact alias lookup in `_exact_hits` is separate, not coordinated
- No typed `ParsedQuery` struct — classification result is a bare `tuple[str|None, float|None]`
  passed ad-hoc through function calls

**Verdict: REFACTOR** — logic is sound but no typed contract, missing expansion, temporality incomplete

---

### Stage 2 — Multi-Channel Recall

**Implemented in:** `_exact_hits`, `_bm25_hits`, `_semantic_hits`, `graph.graph_expand`,
`_archive_hits` — all called sequentially inside `retrieve()`

**What works:**
- All 5 channels functional
- Candidate pool merging via dict (note_id → `_Candidate`)
- Graph expansion uses bidirectional edges, weighted by relation type
- FTS5 BM25 index with Porter stemming

**Bugs / gaps:**
- **Double embedding compute:** `_semantic_hits()` (line 200) calls
  `build_embedding_provider(config).embed_texts([query])[0]` to compute candidate vectors,
  then `retrieve()` calls it again at line 353 to compute the query vector for cosine scoring —
  query is embedded twice per call
- **Archive channel no policy gate:** triggered solely by `not ranked_hits and include_archive`
  (line 518). No query type check, no temporal signal check, no minimum wiki-hit threshold.
  Personal preference queries (`preference_identity`) should never fall through to raw archive.
- Channels run **sequentially** (no parallelism) — blocking I/O in BM25 and semantic can
  serialize unnecessarily
- `_archive_hits` queries `chunks_fts` FTS table — verify this name matches migration schema
  (migration uses `archive_chunks` as base table; FTS virtual table name needs cross-check)
- No typed `CandidatePool` — candidates stored as a plain `dict[str, _Candidate]`
- Graph channel cold-start: if exact/BM25/semantic all miss, graph channel skipped entirely
  (line 379: `if config.graph.enabled and candidates`) — acceptable but undocumented

**Verdict: EXTEND** — algorithms correct, fix double-encode and archive policy

---

### Stage 3 — Signal Integration

**Implemented in:** `retrieve()` lines 410–439, signal loaders in `trust.py`

**What works:**
- Trust signals loaded from `note_trust` via `load_note_trust()`
- Freshness signals loaded from `note_freshness` via `load_note_freshness()`
- Importance scores loaded from `note_importance` table (lines 413–420)
- Freshness state available from maintenance-computed `note_freshness` table

**Bugs / gaps:**
- **`importance_score` loaded but never used in scoring formula** — lines 413–420 perform
  a DB query and populate `importance_map`, line 627 logs it to `retrieval_hit_features`,
  but lines 473–482 (the scoring formula) do not include any `importance_weight` term.
  Importance is dead code in the retrieval path.
- Signal loading is **not a discrete stage** — it is interleaved with scoring loop
- No typed `NoteSignals` struct — trust and freshness are separate dicts, importance is
  a third dict, all merged ad-hoc
- Content quality gate (`_hit_has_content_quality`) lives in `runtime.py` — wrong module;
  quality is a Stage 3 concern (signal about note content), not a Stage 6 concern (assessment)
- Fallback when `note_freshness` row is absent: `freshness_state = "fresh"` (line 435) —
  this is optimistic; a note with no freshness record is unknown, not fresh

**Verdict: REFACTOR** — signals exist in DB, loading works, but importance is dead, wrong
module placement, no typed contract

---

### Stage 4 — Reranking

**Implemented in:** `retrieve()` lines 441–514, `trust.train_rerank_model()` (lines 247–317)

**What works:**
- Multi-factor scoring formula with 7 weighted components
- Freshness multiplier applied as a post-score penalty/relief
- Historical bonus for stale notes under historical queries
- `rerank_weights` table loaded via `load_rerank_model()` — config weights used as fallback
- `train_rerank_model()` function exists and produces weight updates

**Bugs / gaps:**
- **`importance_score` absent from formula** (see Stage 3 — same root cause)
- **`train_rerank_model()` is never called** — no daemon job, no script, no trigger. The
  function exists but is unreachable from any production code path. Training requires
  manual Python invocation.
- Training algorithm is crude: per-feature mean delta × 0.25 scale, not gradient descent.
  Cannot overfit but also cannot learn non-linear patterns.
- `DEFAULT_RERANK_WEIGHTS` in `trust.py` (line 33–40) has `"trust_weight": 0.15` but
  `config.retrieval.memory_strength_weight` is 0.10 — key name mismatch means trained
  weights may not override the config fallback correctly
- Archive hits bypass the scoring formula entirely — assigned `final_score = 1 - (rank-1)/top_k`
  (line 521) — meaningless relative ordering with no signals applied
- Candidate deduplication only keeps first-seen channel; if a note appears in BM25 then
  semantic, the semantic score is added to the existing candidate (correct), but exact
  rank deduplication is not consistently applied when a note appears in both exact and BM25
- `bm25_max_rank` normalization (line 423) uses the pool maximum, which changes as candidates
  are added — normalization is not stable across runs

**Verdict: EXTEND** — fix importance weight, trigger training, fix key name mismatch

---

### Stage 5 — Assembly

**Implemented in:** `runtime._build_memory_packet()` (lines 391–465),
`_load_same_episode_context()` (lines 468–534)

**What works:**
- Type-based slot composition: procedure/fact/episode/context buckets
- Per-bucket caps to prevent one type dominating
- `same_episode_context` loaded via `chain_id` match on canonical notes
- Relation-based slot routing: timeline/similarity/contrast/topic
- Warnings for stale/contested hits

**Bugs / gaps (critical):**
- **Archive hits appear in `primary_memories`** — `retrieve()` line 539:
  `ranked_hits = archive_hits` — archive chunks are merged into the top-level `hits` list.
  `_build_memory_packet()` then iterates over `retrieval_result.hits` and skips only
  `archive_chunk` type at line 410 — but archive hits assigned `type="archive_chunk"` so
  the skip works. Re-check: line 528 assigns `type="archive_chunk"` — this skip DOES work.
  **Correction:** archive hits DO appear to be filtered by the `continue` at line 411.
  However, `RetrieveResult.hits` contains both wiki and archive chunks together — the
  separation is implicit (type check), not structural. No `archive_context` slot exists
  in `RetrieveResult` or `MemoryPacket`.
- **No `archive_context` slot** — the protocol contract requires archive hits in a
  separate `archive_context[]` slot; they are currently invisible to callers (filtered out
  of packet but not surfaced separately)
- **No cross-slot deduplication** — a note can appear in both primary and related slots
  if it was added to `selected_ids` after the related loop started (unlikely but possible
  with current loop structure)
- **No context budget enforcement** — token count not tracked; no body truncation on budget
- **No contradiction detection** — supersedes/superseded_by edge pairs not annotated
- `same_episode_context` requires `primary.chain_id` non-null — notes without chain_id
  never get episode context, even if same session (acceptable limitation, documented)
- Related slot routing uses `hit.relation_types` which is empty for non-graph hits —
  BM25/semantic hits always route to "topic" bucket regardless of actual relations

**Verdict: EXTEND** — add archive_context slot, add dedup, budget enforcement, contradiction annotation

---

### Stage 6 — Assessment

**Implemented in:** `runtime._assess_hits()` (lines 289–376),
`runtime._hit_has_content_quality()` (lines 261–286)

**What works:**
- Full retrieval state classification: sufficient_current, sufficient_historical, partial,
  insufficient_no_hit, insufficient_stale, insufficient_contested
- Freshness-based bucket sorting: fresh/aging → current_ids, suspect → partial_ids,
  stale/contested → stale/contested_ids
- **Content quality gate implemented** — downgrade `sufficient_current` → `partial` when
  top hit has no actionable content signal (WHEN/DO/Steps/Cause/Fix markers, 20+ word excerpt)
- Verification action derivation

**Bugs / gaps:**
- Quality gate checks **only top_hit** (line 364) — if top hit passes but second hit would
  fail, no downgrade occurs. Spec says all primary hits should pass.
- `sufficient_historical` always returns `recommendation="reuse"` (line 333) — historical
  material is stale by definition; `"reuse_with_verification"` would be safer
- Assessment is called twice on some code paths: once via `assess_retrieval()` (Python API)
  and once via `assess_retrieval_payload()` (shell script JSON path) — duplicate logic
  maintained in two call sites

**Verdict: EXTEND** — quality gate exists (good), fix historical recommendation, extend gate to all primary hits

---

### Stage 7 — Emission

**Implemented in:** `retrieval.retrieve()` (logging, lines 541–630),
`retrieval.result_to_json()` (lines 641–649),
`scripts/mnemonic_context.sh` (formatting)

**What works:**
- `retrieval_queries`, `retrieval_hits`, `retrieval_hit_features` tables logged per call
- JSON and Markdown output paths
- Pinned paths included in output

**Bugs / gaps:**
- **Emission logging inside Stage 2/4** — the `INSERT INTO retrieval_queries` block at
  line 541 is inside `retrieve()`, not a discrete emission stage. Logging side-effects
  mixed with retrieval logic.
- `result_to_json()` (line 641) does not include `retrieval_state`, `recommendation`, or
  `verification_action` — these fields only appear in the runtime path (`resolve_task_context`)
- No `archive_context` field in `RetrieveResult` dataclass — emission cannot surface
  archive hits in a separate slot
- Two emission paths (direct `retrieve()` + `mnemonic_context.sh` / `resolve_task_context`)
  diverge: direct path lacks assessment, runtime path has full assessment

**Verdict: EXTEND** — add archive_context to RetrieveResult, add assessment to direct path output

---

## Cross-Cutting Findings

### Mutation Safety

No wiki mutations detected in the retrieval read path. The `retrieve()` function opens a
connection and writes only to: `retrieval_queries`, `retrieval_hits`, `retrieval_hit_features`,
`retrieval_archive_hits` — all instrumentation tables, not canonical wiki state. **PASS.**

### Eval Coverage

82 miss candidates in `evals/candidates.jsonl`. Based on stage analysis, likely failure
distribution:

| Stage | Suspected miss root cause |
|-------|--------------------------|
| Stage 1 | Query classification wrong type → wrong type priors |
| Stage 2 | No synonym expansion → BM25 misses procedure note with different vocabulary |
| Stage 3 | Importance not in formula → highly-connected relevant note ranked low |
| Stage 4 | Training never triggered → weights stuck at defaults |
| Stage 5 | Archive hits not surfaced when wiki genuinely empty |
| Stage 6 | Empty-body notes returned as sufficient_current (quality gate partial) |

### Importance Score Dead Code

The most impactful dead code: `importance_score` is computed by the maintenance layer
(`wiki_importance_scoring` daemon job), stored in `note_importance`, loaded per-query
(DB round-trip), logged to `retrieval_hit_features`, but **contributes zero to final_score**.
Every query pays the load cost with no benefit.

---

## Overall Verdict

**EXTEND** the legacy implementation. The core algorithms are sound and the DB schema
is well-designed. The problems are fixable without architectural replacement:

1. Two critical bugs: importance dead code, training never triggered
2. Missing stage boundaries (monolithic `retrieve()`)
3. Missing features: archive_context slot, expansion, dedup, budget, full quality gate
4. Naming inconsistency between `trust.py` weight keys and config keys

A clean reimplementation following the 7-stage contract in
`brain/system/east-flange-development-plan.md` would organize existing logic into discrete
modules with typed I/O. No algorithmic replacement is required for Phases 1–2.

---

## Files Assessed

| File | Path |
|------|------|
| Core retrieval | `src/second_brain/retrieval.py` |
| Trust/freshness/training | `src/second_brain/trust.py` |
| Graph expansion | `src/second_brain/graph.py` |
| Assembly/assessment/protocol | `src/second_brain/runtime.py` |
| Assessment payload / writeback | `src/second_brain/bridge.py` |
| Embedding providers | `src/second_brain/embedding_providers.py` |
| Shell entry point | `scripts/mnemonic_context.sh` |
| Eval miss candidates | `evals/candidates.jsonl` |

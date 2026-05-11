# Ingestion: East-Flange Plan — Stage 1

## Purpose

Stage 1 transforms a raw natural-language query (plus optional caller hints) into a
typed `ParsedQuery` struct. This struct is the single input contract for Stage 2
(Multi-Channel Recall). All downstream stages depend on `ParsedQuery` for query type,
temporality, expanded terms, and scope.

Stage 1 is the only east flange stage that the caller's hints (token_budget, temporality,
tags) enter the pipeline. After Stage 1, all downstream stages are hint-free.

---

## Contract

```
Input:
  raw_query: str                    — natural language, unmodified
  hints: QueryHints (optional)      — caller-supplied metadata

Output:
  ParsedQuery                       — typed, immutable
```

**QueryHints fields:**
- `query_type_hint: str | None` — override classifier; one of the 6 known types
- `temporality_override: str | None` — force temporality: current | historical | mixed | unknown
- `token_budget: int | None` — caller's context window budget
- `scope: QueryScope | None` — workspace path, repo name, tags
- `top_k: int | None` — requested result count (passed through, not used in Stage 1)

**ParsedQuery fields:**
- `raw: str` — original query string, unmodified
- `tokens: list[str]` — lowercased, stopword-filtered, deduplicated tokens
- `query_type: str | None` — classified type; None if unclassifiable
- `classifier_confidence: float` — [0.0, 1.0]; 0.0 if unclassified; 1.0 if from hint
- `temporality: str` — "current" | "historical" | "mixed" | "unknown"
- `anchor_terms: list[str]` — raw tokens likely to match aliases or note ID prefixes
- `expanded_terms: list[str]` — additional terms from synonym map and alias table lookup
- `scope: QueryScope | None` — passed through from hints
- `token_budget: int | None` — passed through from hints
- `top_k: int` — resolved top_k (hint or config default)

**Immutability:** `ParsedQuery` is a frozen dataclass. No stage may mutate it.

---

## Phase S1-P1 — ParsedQuery Contract and Tokenizer

### Goal

Define `ParsedQuery`, `QueryHints`, `QueryScope` as canonical typed dataclasses.
Implement the tokenizer that produces `ParsedQuery.tokens` from a raw query string.

### Specification

**Tokenizer rules:**
1. Extract tokens matching Unicode word boundary pattern: `[\w/-]+`
2. Lowercase all tokens
3. Remove stopwords (see list below)
4. Remove tokens shorter than 3 characters
5. Deduplicate while preserving first-occurrence order
6. Return as `list[str]`

**Stopword list:**
```python
{"a", "an", "and", "are", "as", "at", "be", "by", "did", "do",
 "for", "from", "has", "have", "how", "i", "in", "is", "it",
 "its", "me", "my", "of", "on", "or", "our", "that", "the",
 "to", "us", "was", "we", "what", "which", "who", "with"}
```

**Empty query handling:** if tokenizer produces empty list, return `["*"]` as a
wildcard sentinel. Downstream stages handle `["*"]` as a broad-match signal.

### Deliverables

- `src/second_brain/query_parser.py` — module with `ParsedQuery`, `QueryHints`, `QueryScope`
  dataclasses and `tokenize(query: str) -> list[str]` function
- Unit tests: empty string, single token, stopword-only query, Unicode input,
  wildcard fallback

### Acceptance criteria

- `tokenize("how do we deploy the auth service")` returns `["deploy", "auth", "service"]`
- `tokenize("")` returns `["*"]`
- `tokenize("who owns it")` returns `["owns"]`
- `ParsedQuery` is a frozen dataclass with all fields typed

---

## Phase S1-P2 — Query Type Classifier

### Goal

Implement the query type classifier that maps a query's tokens to one of 6 query types
with a confidence score. This replaces the inline `_classify_query()` in `retrieval.py`.

### Specification

**Query types and their keyword signatures:**

| Type | Primary keywords | Secondary keywords |
|------|-----------------|-------------------|
| `preference_identity` | prefer, preference, style, identity | user, settings, personality |
| `how_to` | how, steps, procedure, run, deploy, release, install, setup | do, execute, build |
| `decision` | why, reason, rationale, decide, chose, picked, because, should | decision, choose |
| `who_where_relation` | who, owner, owns, responsible, maintainer, team | where, contact |
| `what_changed` | changed, recent, latest, new, update, modified, diff | since, added, removed |
| `broad_synthesis` | state, status, summary, overview, describe, explain | all, total, list |

**Classification algorithm:**
1. Count token matches against each type's primary keywords (weight 1.0) and
   secondary keywords (weight 0.5)
2. Best type = argmax weighted score
3. Confidence = `min(1.0, 0.45 + 0.20 × primary_match_count + 0.10 × secondary_match_count)`
4. If best weighted score = 0.0: return `(None, 0.0)` — unclassified
5. If `query_type_hint` is provided and is a valid type: return `(hint, 1.0)` — bypass classifier

**Type prior table** (used in Stage 4 reranking, defined here for reference):
```python
TYPE_PRIORS = {
    "preference_identity": {"person": 0.9, "concept": 0.5},
    "how_to":              {"procedure": 0.9, "repo": 0.6, "project": 0.4},
    "decision":            {"decision": 0.9, "incident": 0.6, "project": 0.4},
    "who_where_relation":  {"person": 0.9, "project": 0.7, "repo": 0.5},
    "what_changed":        {"incident": 0.8, "project": 0.7, "decision": 0.6},
    "broad_synthesis":     {"overview": 0.9, "project": 0.7, "concept": 0.5},
}
```

`TYPE_PRIORS` is defined in `query_parser.py` and imported by Stage 4.

### Deliverables

- `classify_query(tokens: list[str], hint: str | None) -> tuple[str | None, float]`
  in `query_parser.py`
- `TYPE_PRIORS: dict[str, dict[str, float]]` constant in `query_parser.py`
- Unit tests: each type's primary keywords, hint override, unclassified query,
  multi-type ambiguity (e.g., "how and why" → how_to wins by weighted score)

### Acceptance criteria

- `classify_query(["deploy", "auth", "service"], None)` returns `("how_to", ≥0.65)`
- `classify_query(["why", "postgres"], None)` returns `("decision", ≥0.65)`
- `classify_query(["hello", "world"], None)` returns `(None, 0.0)`
- `classify_query([], "decision")` returns `("decision", 1.0)`

---

## Phase S1-P3 — Temporality Inference

### Goal

Infer query temporality (current / historical / mixed / unknown) from query content.
Replaces `trust.query_temporality()` with a richer implementation that covers all 4 states.

### Specification

**Historical indicators** (tokens or phrases):
```python
HISTORICAL_INDICATORS = {
    "before", "previously", "historical", "history", "used", "earlier",
    "then", "prior", "ago", "old", "former", "originally", "past",
    "when", "legacy", "was", "were", "had",
}
```

**Current indicators** (tokens or phrases):
```python
CURRENT_INDICATORS = {
    "current", "now", "today", "latest", "currently", "still",
    "active", "live", "present", "existing",
}
```

**Temporality rules:**
1. Count historical tokens in query tokens list
2. Count current tokens in query tokens list
3. If historical > 0 AND current > 0: return `"mixed"`
4. If historical > 0 AND current = 0: return `"historical"`
5. If current > 0 AND historical = 0: return `"current"`
6. If neither: return `"unknown"` (not "current" — defer to Stage 6 assessment)
7. If `temporality_override` hint provided: return hint (bypass inference)

**Design note:** returning `"unknown"` instead of defaulting to `"current"` is a
deliberate change from the legacy. Stage 4 and Stage 6 treat `"unknown"` as
"assume current for scoring but do not penalize historical material as heavily."
This reduces false-positive stale penalties on ambiguous queries.

### Deliverables

- `infer_temporality(tokens: list[str], override: str | None) -> str` in `query_parser.py`
- Unit tests: purely historical query, purely current query, mixed query, ambiguous query,
  override

### Acceptance criteria

- `infer_temporality(["historical", "deploy", "process"], None)` = `"historical"`
- `infer_temporality(["current", "state"], None)` = `"current"`
- `infer_temporality(["before", "now"], None)` = `"mixed"`
- `infer_temporality(["deploy", "auth"], None)` = `"unknown"`
- `infer_temporality(["any", "tokens"], "historical")` = `"historical"`

---

## Phase S1-P4 — Anchor Term Extraction

### Goal

Identify tokens in the query that are high-confidence candidates for exact-match lookup
in Stage 2. Anchor terms are tokens that:
- match a known note ID prefix (`procedure/`, `decision/`, `concept/`, etc.)
- appear in the `aliases` table (case-insensitive)
- match a structural pattern (e.g., `project/`, `repo/`)

Anchor terms enable Stage 2's exact channel to run targeted alias lookups rather than
full-table scans on every query.

### Specification

**Note ID prefix detection:**
```python
NOTE_ID_PREFIXES = {
    "procedure", "decision", "concept", "incident",
    "person", "project", "repo", "source", "journal", "overview",
}
```
If a query token matches `<prefix>/<anything>` pattern, the whole token is an anchor term.

**Alias table lookup:** for tokens with length ≥ 4 (to avoid false positives on short
words), check if `lower(token)` appears in `aliases.alias` column (case-insensitive).
If yes, include token in `anchor_terms`.

**Alias table read is Stage 1's single permitted DB read.** Limit alias lookup to at
most 5 tokens per query (take the 5 longest tokens first) to bound latency.

**Anchor term list:** deduplicated, preserving first-occurrence order. Max 5 entries.

### Deliverables

- `extract_anchor_terms(tokens: list[str], connection: sqlite3.Connection) -> list[str]`
  in `query_parser.py`
- Unit tests: note-ID prefix match, alias match (mock DB), no match (empty result),
  length filter, deduplication

### Acceptance criteria

- `extract_anchor_terms(["procedure/deploy-auth"], db)` = `["procedure/deploy-auth"]`
- `extract_anchor_terms(["foo", "bar"], db)` = `[]` when neither in aliases
- Max 5 entries regardless of input size
- Alias lookup only on tokens with len ≥ 4

---

## Phase S1-P5 — Term Expansion

### Goal

Expand query tokens with domain synonyms and alias-derived terms, so Stage 2's BM25
and semantic channels can match notes that use different vocabulary for the same concept.

### Specification

**Domain synonym map:**
```python
SYNONYM_MAP: dict[str, list[str]] = {
    "deploy":    ["release", "rollout", "publish"],
    "release":   ["deploy", "publish"],
    "decision":  ["reason", "rationale", "choice"],
    "owner":     ["maintainer", "responsible", "contact"],
    "incident":  ["outage", "failure", "bug"],
    "database":  ["db", "storage", "postgres", "sqlite"],
    "procedure": ["steps", "guide", "how-to"],
    "concept":   ["idea", "pattern", "definition"],
    "setup":     ["install", "configure", "init"],
    "debug":     ["investigate", "diagnose", "troubleshoot"],
}
```

**Expansion algorithm:**
1. For each token in `ParsedQuery.tokens`:
   - If token in `SYNONYM_MAP`: add up to 2 synonyms from the list
2. For each anchor term that matched an alias:
   - Fetch the canonical note title for that alias and tokenize it
   - Add non-stopword title tokens not already in tokens list
3. Deduplication: remove any expanded term already in original tokens
4. Cap: `expanded_terms` max length = 3 (prevents recall explosion)
5. Terms that would be over the cap are dropped in order of synonym map priority

**No DB round-trip in this phase** beyond the alias lookup already done in S1-P4.
Title tokens are derived from the anchor term record retrieved in S1-P4.

### Deliverables

- `SYNONYM_MAP: dict[str, list[str]]` constant in `query_parser.py`
- `expand_terms(tokens: list[str], anchor_terms: list[str],
                alias_titles: dict[str, str]) -> list[str]` in `query_parser.py`
- Unit tests: synonym hit, multi-synonym (cap enforcement), no synonyms, alias title expansion

### Acceptance criteria

- `expand_terms(["deploy"], [], {})` = `["release", "rollout"]` (2 synonyms, cap not hit)
- `expand_terms(["deploy", "incident", "decision"], [], {})` produces at most 3 terms
- `expand_terms(["deploy"], [], {})` does not include "deploy" in expanded list
  (already in tokens)
- `expand_terms([], ["procedure/deploy-auth"], {"procedure/deploy-auth": "Deploy Auth Service"})`
  includes tokens from "Deploy Auth Service" not already in query

---

## Phase S1 Integration — ParsedQuery Assembly

### Goal

Assemble all S1-P1 through S1-P5 outputs into a single `ParsedQuery` struct.
Implement the public `parse_query(raw, hints, config, connection) -> ParsedQuery` function.

### Specification

```python
def parse_query(
    raw: str,
    hints: QueryHints | None = None,
    *,
    config: AppConfig,
    connection: sqlite3.Connection | None = None,
) -> ParsedQuery:
    ...
```

**Assembly order:**
1. Tokenize → `tokens`
2. Classify → `query_type`, `classifier_confidence`
3. Infer temporality → `temporality`
4. Extract anchor terms (requires DB connection; skip if None) → `anchor_terms`
5. Expand terms → `expanded_terms`
6. Resolve `top_k` from `hints.top_k or config.retrieval.top_k`
7. Return frozen `ParsedQuery`

**Connection-optional:** if `connection` is None, `anchor_terms = []` and
`expanded_terms` use synonym map only (no alias-derived titles).

### Deliverables

- `parse_query()` top-level function in `query_parser.py`
- Integration test: full query string with known aliases in test DB → verify all fields
- Integration test: `connection=None` path → verify degraded gracefully

### Acceptance criteria

- `parse_query("how do we deploy auth", connection=db)` produces:
  - `tokens = ["deploy", "auth"]`
  - `query_type = "how_to"`
  - `classifier_confidence ≥ 0.65`
  - `temporality = "unknown"`
  - `expanded_terms` includes "release" or "rollout"
- No exception when `connection=None`
- `ParsedQuery` is frozen (assignment raises `FrozenInstanceError`)

---

## Stage 1 Completion Gate

Stage 1 is complete when:

1. `parse_query()` is the sole entry point (no inline classification in `retrieve()`)
2. `ParsedQuery` accepted by Stage 2 `recall()` in end-to-end integration test
3. All S1 unit tests pass
4. `query_temporality()` in `trust.py` is deprecated (may stay for compatibility,
   but `parse_query` is the canonical path)
5. No regressions on `evals/queries.jsonl` top1 recall versus legacy baseline

---

## What This Stage Does Not Cover

- BM25 query construction from expanded terms (Stage 2)
- FTS5 index management (Stage 2)
- Embedding compute (Stage 2 / Stage 3)
- Query type priors applied to scoring (Stage 4)
- Any DB write (Stage 1 is read-only for alias lookup only)

---

## Module: `query_parser.py`

**Exported symbols:**
```python
# Dataclasses
ParsedQuery
QueryHints
QueryScope

# Constants
SYNONYM_MAP
TYPE_PRIORS

# Functions
parse_query(raw, hints, *, config, connection) -> ParsedQuery
tokenize(query: str) -> list[str]
classify_query(tokens, hint) -> tuple[str | None, float]
infer_temporality(tokens, override) -> str
extract_anchor_terms(tokens, connection) -> list[str]
expand_terms(tokens, anchor_terms, alias_titles) -> list[str]
```

**Not exported:**
- Stopword set (implementation detail)
- Keyword signature dicts (implementation detail, exposed only for testing)
- Historical/current indicator sets (implementation detail)

---

## Governing References

- `brain/system/ingestion-ingestion-east-flange-overview.md` — Stage 1 full specification
- `brain/system/ingestion-east-flange-roadmap.md` — completion tracking
- `brain/system/runtime-protocol.md` — QueryHints and task metadata contract

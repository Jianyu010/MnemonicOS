# East Flange Plan — Retrieval Path Stage 1: Query Parsing

## Purpose

The east flange is Stage 1 of the retrieval path — the query intake boundary,
symmetric to the west flange on the ingestion side. It transforms a raw
natural-language query (plus optional caller hints) into a typed, frozen
`ParsedQuery` struct that all downstream retrieval stages consume.

"East flange" refers specifically to this stage. Stages 2–7 are internal
retrieval components, not east flange.

---

## Contract

```
Input:
  raw_query: str                    — natural language, unmodified
  hints: QueryHints (optional)      — caller-supplied metadata

Output:
  ParsedQuery                       — typed, frozen
```

**QueryHints fields:**
- `query_type_hint: str | None` — override classifier; one of the 6 known types
- `temporality_override: str | None` — force: current | historical | mixed | unknown
- `token_budget: int | None` — caller's context window budget
- `scope: QueryScope | None` — workspace path, repo name, tags
- `top_k: int | None` — requested result count (passed through, not used in Stage 1)

**ParsedQuery fields:**
- `raw: str` — original query string, unmodified
- `tokens: list[str]` — lowercased, stopword-filtered, deduplicated
- `query_type: str | None` — classified type; None if unclassifiable
- `classifier_confidence: float` — [0.0, 1.0]; 1.0 if from hint
- `temporality: str` — "current" | "historical" | "mixed" | "unknown"
- `anchor_terms: list[str]` — tokens matching aliases or note ID prefixes
- `expanded_terms: list[str]` — additional terms from synonym map + alias titles
- `scope: QueryScope | None` — passed through from hints
- `token_budget: int | None` — passed through from hints
- `top_k: int` — resolved (hint or config default)

**Immutability:** `ParsedQuery` is a frozen dataclass. No stage may mutate it.

**DB access:** one alias-table read (anchor extraction). No writes.

---

## Phase S1-P1 — ParsedQuery Contract and Tokenizer

### Goal

Define all Stage 1 dataclasses. Implement the tokenizer that produces
`ParsedQuery.tokens`.

### Specification

**Tokenizer rules:**
1. Extract tokens matching: `[\w/-]+` (Unicode)
2. Lowercase all tokens
3. Remove stopwords
4. Remove tokens shorter than 3 characters
5. Deduplicate preserving first-occurrence order

**Stopwords:**
```python
{"a", "an", "and", "are", "as", "at", "be", "by", "did", "do",
 "for", "from", "has", "have", "how", "i", "in", "is", "it",
 "its", "me", "my", "of", "on", "or", "our", "that", "the",
 "to", "us", "was", "we", "what", "which", "who", "with"}
```

**Empty query:** if token list empty after filtering, return `["*"]` (wildcard sentinel).

### Deliverables

- `src/second_brain/query_parser.py` with `ParsedQuery`, `QueryHints`, `QueryScope`,
  and `tokenize(query: str) -> list[str]`
- Unit tests: empty string → `["*"]`; stopword-only → `["*"]`; Unicode input;
  deduplication; short-token filter

### Acceptance criteria

- `tokenize("how do we deploy the auth service")` → `["deploy", "auth", "service"]`
- `tokenize("")` → `["*"]`
- `tokenize("who owns it")` → `["owns"]`
- `ParsedQuery` is frozen (assignment raises `FrozenInstanceError`)

---

## Phase S1-P2 — Query Type Classifier

### Goal

Classify the query into one of 6 types with confidence score.
Replaces inline `_classify_query()` in `retrieval.py`.

### Specification

**Query types and keyword signatures:**

| Type | Primary keywords | Secondary keywords |
|------|-----------------|-------------------|
| `preference_identity` | prefer, preference, style, identity | user, settings, personality |
| `how_to` | how, steps, procedure, run, deploy, release, install, setup | do, execute, build |
| `decision` | why, reason, rationale, decide, chose, picked, because, should | decision, choose |
| `who_where_relation` | who, owner, owns, responsible, maintainer, team | where, contact |
| `what_changed` | changed, recent, latest, new, update, modified, diff | since, added, removed |
| `broad_synthesis` | state, status, summary, overview, describe, explain | all, total, list |

**Algorithm:**
1. Count primary matches (weight 1.0) + secondary matches (weight 0.5) per type
2. Best type = argmax weighted score
3. Confidence = `min(1.0, 0.45 + 0.20 × primary_count + 0.10 × secondary_count)`
4. Score = 0: return `(None, 0.0)`
5. `query_type_hint` provided + valid: return `(hint, 1.0)`

**Type prior table** (used in Stage 4, defined here as canonical):
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

### Deliverables

- `classify_query(tokens, hint) -> tuple[str | None, float]` in `query_parser.py`
- `TYPE_PRIORS` constant in `query_parser.py` (imported by Stage 4)
- Unit tests: each type's keywords, hint override, unclassified, ambiguous multi-type

### Acceptance criteria

- `classify_query(["deploy", "auth", "service"], None)` → `("how_to", ≥0.65)`
- `classify_query(["why", "postgres"], None)` → `("decision", ≥0.65)`
- `classify_query(["hello", "world"], None)` → `(None, 0.0)`
- `classify_query([], "decision")` → `("decision", 1.0)`

---

## Phase S1-P3 — Temporality Inference

### Goal

Infer query temporality across 4 states. Replaces `trust.query_temporality()`,
which only returns `"current"` or `"historical"` and defaults to `"current"` for
ambiguous queries.

### Specification

**Historical indicators:**
```python
HISTORICAL_INDICATORS = {
    "before", "previously", "historical", "history", "used", "earlier",
    "then", "prior", "ago", "old", "former", "originally", "past",
    "when", "legacy", "was", "were", "had",
}
```

**Current indicators:**
```python
CURRENT_INDICATORS = {
    "current", "now", "today", "latest", "currently", "still",
    "active", "live", "present", "existing",
}
```

**Rules:**
1. historical > 0 AND current > 0 → `"mixed"`
2. historical > 0 AND current = 0 → `"historical"`
3. current > 0 AND historical = 0 → `"current"`
4. neither → `"unknown"` (not `"current"`)
5. `temporality_override` provided → return override (bypass)

`"unknown"` means: assume current for scoring but do not apply stale penalties
aggressively. Stages 4 and 6 treat `"unknown"` as a softer form of `"current"`.

### Deliverables

- `infer_temporality(tokens, override) -> str` in `query_parser.py`
- Unit tests: historical-only, current-only, mixed, ambiguous, override

### Acceptance criteria

- `infer_temporality(["historical", "deploy"], None)` → `"historical"`
- `infer_temporality(["current", "state"], None)` → `"current"`
- `infer_temporality(["before", "now"], None)` → `"mixed"`
- `infer_temporality(["deploy", "auth"], None)` → `"unknown"`
- `infer_temporality(["anything"], "historical")` → `"historical"`

---

## Phase S1-P4 — Anchor Term Extraction

### Goal

Identify tokens that are high-confidence candidates for exact-match lookup in Stage 2.

### Specification

**Note ID prefix detection:**
```python
NOTE_ID_PREFIXES = {
    "procedure", "decision", "concept", "incident",
    "person", "project", "repo", "source", "journal", "overview",
}
```
Token matching `<prefix>/<anything>` → anchor term (whole token).

**Alias table lookup:** for tokens with `len ≥ 4`, check if `lower(token)` appears
in `aliases.alias` (case-insensitive). Limit to 5 longest tokens per query to
bound latency.

**Output:** deduplicated list, max 5 entries, preserving first-occurrence order.

This is Stage 1's single permitted DB read.

### Deliverables

- `extract_anchor_terms(tokens, connection) -> list[str]` in `query_parser.py`
- Unit tests: note-ID prefix match; alias match (mock DB); no match; length filter;
  max-5 cap; `connection=None` returns `[]`

### Acceptance criteria

- `extract_anchor_terms(["procedure/deploy-auth"], db)` → `["procedure/deploy-auth"]`
- `extract_anchor_terms(["foo", "bar"], db)` → `[]` when neither in aliases
- Returns at most 5 entries
- Alias lookup only on `len(token) ≥ 4`

---

## Phase S1-P5 — Term Expansion

### Goal

Add synonym and alias-derived terms so Stage 2 BM25/semantic channels match notes
using different vocabulary for the same concept. Cap at 3 additional terms.

### Specification

**Domain synonym map:**
```python
SYNONYM_MAP = {
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

**Algorithm:**
1. For each token in `ParsedQuery.tokens`: if in `SYNONYM_MAP`, add up to 2 synonyms
2. For each anchor term with a known alias title: tokenize title, add non-stopword
   title tokens not already in `tokens`
3. Remove any term already in `tokens` (original terms not duplicated in expansion)
4. Cap: `expanded_terms` max length = 3

No additional DB round-trips beyond S1-P4.

### Deliverables

- `SYNONYM_MAP` constant in `query_parser.py`
- `expand_terms(tokens, anchor_terms, alias_titles) -> list[str]` in `query_parser.py`
- Unit tests: synonym hit, cap enforcement, alias-title expansion, no-op case

### Acceptance criteria

- `expand_terms(["deploy"], [], {})` → `["release", "rollout"]`
- `expand_terms(["deploy", "incident", "decision"], [], {})` produces at most 3 terms
- Expanded terms do not duplicate anything in `tokens`

---

## Phase S1 Integration — ParsedQuery Assembly

### Goal

Assemble all phase outputs into a single `ParsedQuery` via the public `parse_query()`
entry point.

### Specification

```python
def parse_query(
    raw: str,
    hints: QueryHints | None = None,
    *,
    config: AppConfig,
    connection: sqlite3.Connection | None = None,
) -> ParsedQuery:
```

**Assembly order:**
1. `tokenize(raw)` → `tokens`
2. `classify_query(tokens, hints.query_type_hint)` → `query_type`, `classifier_confidence`
3. `infer_temporality(tokens, hints.temporality_override)` → `temporality`
4. `extract_anchor_terms(tokens, connection)` → `anchor_terms` (skip if `connection=None`)
5. `expand_terms(tokens, anchor_terms, alias_titles)` → `expanded_terms`
6. Resolve `top_k` from `hints.top_k or config.retrieval.top_k`
7. Return frozen `ParsedQuery`

**Degraded path:** `connection=None` → `anchor_terms=[]`, `expanded_terms` from
synonym map only.

### Deliverables

- `parse_query()` top-level function in `query_parser.py`
- Integration test: full query against test DB with seeded aliases, verify all fields
- Integration test: `connection=None` degraded path

### Acceptance criteria

- `parse_query("how do we deploy auth", connection=db)` →
  `query_type="how_to"`, `tokens=["deploy","auth"]`, `temporality="unknown"`,
  `expanded_terms` includes "release" or "rollout"
- `ParsedQuery` is frozen; assignment raises `FrozenInstanceError`
- No exception when `connection=None`

---

## Completion Gate

Stage 1 (east flange) is complete when:

1. `parse_query()` is the sole query-parsing entry point — no inline
   `_classify_query()` in `retrieval.py`
2. `ParsedQuery` accepted by Stage 2 `recall()` in end-to-end integration test
3. All S1 unit and integration tests pass
4. `trust.query_temporality()` deprecated as canonical path (may stay for
   compatibility, but `parse_query` governs)
5. No regressions on `evals/queries.jsonl` top-1 recall vs. legacy baseline

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

# Public API
parse_query(raw, hints, *, config, connection) -> ParsedQuery

# Utilities (exposed for testing)
tokenize(query: str) -> list[str]
classify_query(tokens, hint) -> tuple[str | None, float]
infer_temporality(tokens, override) -> str
extract_anchor_terms(tokens, connection) -> list[str]
expand_terms(tokens, anchor_terms, alias_titles) -> list[str]
```

**Implementation detail (not exported):** stopwords, historical/current indicator
sets, keyword signature dicts.

---

## What This Stage Does Not Cover

- BM25 query construction from expanded terms (Stage 2)
- Embedding compute (Stage 2, 3)
- Type priors applied to candidate scoring (Stage 4)
- Any DB writes

---

## Governing References

- [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md) — full pipeline context
- [retrieval-east-flange-roadmap.md](retrieval-east-flange-roadmap.md) — phase completion status
- [retrieval-development-roadmap.md](retrieval-development-roadmap.md) — overall retrieval roadmap

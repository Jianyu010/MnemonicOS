# East Flange Phase 4: Semantic Type Classifier

## Goal

Classify the query into one of six semantic types with a confidence score. The
result drives `TYPE_PRIORS` in Stage 4 reranking. This phase replaces the inline
`_classify_query()` in `retrieval.py` with a richer, well-documented version in
`query_parser.py`.

This classifier answers "what is being asked" — orthogonal to persona (Phase 3),
which answers "who is asking and what output shape do they need."

## Scope

- `classify_query()` function in `query_parser.py`
- `QUERY_KEYWORD_SIGNATURES` constant (renamed and expanded from `QUERY_HINTS`)
- `TYPE_PRIORS` constant (moved from `retrieval.py`)
- Deletion of `_classify_query()` and `QUERY_HINTS` and `TYPE_PRIORS` from
  `retrieval.py` (happens atomically with S1-INT)

## Components

```
src/second_brain/query_parser.py     — classify_query(), QUERY_KEYWORD_SIGNATURES, TYPE_PRIORS
src/second_brain/retrieval.py        — remove _classify_query(), QUERY_HINTS, TYPE_PRIORS
                                       import TYPE_PRIORS from query_parser after migration
```

## Query Types

| Type | Primary keywords | Secondary keywords |
|------|-----------------|-------------------|
| `preference_identity` | prefer, preference, style, identity | user, settings, personality |
| `how_to` | how, steps, procedure, run, deploy, release, install, setup | do, execute, build |
| `decision` | why, reason, rationale, decide, chose, picked, because, should | decision, choose |
| `who_where_relation` | who, owner, owns, responsible, maintainer, team | where, contact |
| `what_changed` | changed, recent, latest, new, update, modified, diff | since, added, removed |
| `broad_synthesis` | state, status, summary, overview, describe, explain | all, total, list |

Primary keywords carry weight 1.0. Secondary keywords carry weight 0.5.

## Constants

```python
QUERY_KEYWORD_SIGNATURES: dict[str, tuple[set[str], set[str]]] = {
    "preference_identity": (
        {"prefer", "preference", "style", "identity"},
        {"user", "settings", "personality"},
    ),
    "how_to": (
        {"how", "steps", "procedure", "run", "deploy", "release", "install", "setup"},
        {"do", "execute", "build"},
    ),
    "decision": (
        {"why", "reason", "rationale", "decide", "chose", "picked", "because", "should"},
        {"decision", "choose"},
    ),
    "who_where_relation": (
        {"who", "owner", "owns", "responsible", "maintainer", "team"},
        {"where", "contact"},
    ),
    "what_changed": (
        {"changed", "recent", "latest", "new", "update", "modified", "diff"},
        {"since", "added", "removed"},
    ),
    "broad_synthesis": (
        {"state", "status", "summary", "overview", "describe", "explain"},
        {"all", "total", "list"},
    ),
}

TYPE_PRIORS: dict[str, dict[str, float]] = {
    "preference_identity": {"person": 0.9, "concept": 0.5},
    "how_to":              {"procedure": 0.9, "repo": 0.6, "project": 0.4},
    "decision":            {"decision": 0.9, "incident": 0.6, "project": 0.4},
    "who_where_relation":  {"person": 0.9, "project": 0.7, "repo": 0.5},
    "what_changed":        {"incident": 0.8, "project": 0.7, "decision": 0.6},
    "broad_synthesis":     {"overview": 0.9, "project": 0.7, "concept": 0.5},
}
```

`TYPE_PRIORS` is consumed by Stage 4 reranking. It is defined here as the
canonical source and imported by `reranker.py` (Stage 4) when implemented.

## Algorithm

```python
def classify_query(
    tokens: list[str],
    hint: str | None,
) -> tuple[str | None, float]:
    """
    Returns (query_type, confidence).

    query_type is one of the six keys in QUERY_KEYWORD_SIGNATURES, or None.
    confidence is in [0.0, 1.0].

    If hint is provided and is a valid type key, return (hint, 1.0) immediately.
    """
    if hint and hint in QUERY_KEYWORD_SIGNATURES:
        return hint, 1.0

    token_set = set(tokens)
    best_type: str | None = None
    best_score = 0.0

    for query_type, (primary_kw, secondary_kw) in QUERY_KEYWORD_SIGNATURES.items():
        primary_hits = len(token_set & primary_kw)
        secondary_hits = len(token_set & secondary_kw)
        score = primary_hits * 1.0 + secondary_hits * 0.5
        if score > best_score:
            best_type = query_type
            best_score = score

    if best_type is None or best_score == 0.0:
        return None, 0.0

    confidence = min(1.0, 0.45 + 0.20 * primary_hits + 0.10 * secondary_hits)
    return best_type, confidence
```

### Confidence formula

`confidence = min(1.0, 0.45 + 0.20 × primary_count + 0.10 × secondary_count)`

Where `primary_count` and `secondary_count` are the hit counts for the *winning*
type, not the sum across all types. Base of 0.45 ensures even a single primary
keyword yields confidence ≥ 0.65.

### Tie-breaking

When two types produce equal weighted scores, the first type encountered in the
iteration order of `QUERY_KEYWORD_SIGNATURES` wins. This is deterministic (dicts
are insertion-ordered in Python 3.7+). Ambiguous multi-type queries produce lower
confidence, which downstream stages may use to widen retrieval.

### hint validation

An invalid `hint` value (not a key in `QUERY_KEYWORD_SIGNATURES`) is ignored and
the classifier runs normally. This prevents callers from injecting arbitrary type
strings.

## Migration from retrieval.py

Steps:
1. This phase implements `classify_query()` and `TYPE_PRIORS` in `query_parser.py`
2. S1-INT atomically updates `retrieval.py` to import from `query_parser`:
   ```python
   from .query_parser import TYPE_PRIORS, classify_query as _classify_query_impl
   ```
3. The local `_classify_query()`, `QUERY_HINTS`, and `TYPE_PRIORS` definitions in
   `retrieval.py` are deleted
4. The expanded `QUERY_KEYWORD_SIGNATURES` supersedes the narrower `QUERY_HINTS`

The keyword expansion (primary/secondary split, new keywords) improves
classification accuracy and confidence calibration compared to the legacy version.

## Failure Policy

`classify_query()` never raises. Empty `tokens` with no `hint` returns
`(None, 0.0)`. Any iterable `tokens` is accepted. Non-string elements in `tokens`
are a caller contract violation and not handled.

## Verification

- `classify_query(["deploy", "auth", "service"], None)` → `("how_to", ≥ 0.65)`
- `classify_query(["why", "postgres"], None)` → `("decision", ≥ 0.65)`
- `classify_query(["hello", "world"], None)` → `(None, 0.0)`
- `classify_query([], "decision")` → `("decision", 1.0)`
- `classify_query(["deploy"], "decision")` → `("decision", 1.0)` (hint wins)
- `classify_query(["deploy"], "invalid_type")` → `("how_to", ≥ 0.65)` (invalid hint ignored)
- `classify_query([], None)` → `(None, 0.0)`
- `classify_query(["who", "owns", "team"], None)` → `("who_where_relation", ≥ 0.65)`
- `classify_query(["state", "status", "overview"], None)` → `("broad_synthesis", ≥ 0.85)`
- `TYPE_PRIORS["how_to"]["procedure"]` → `0.9`
- `TYPE_PRIORS` importable from `query_parser` module

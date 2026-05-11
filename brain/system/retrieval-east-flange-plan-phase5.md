# East Flange Phase 5: Temporality Inference

## Goal

Infer the time focus of the query across four states. Replaces `query_temporality()`
in `trust.py`, which returns only two states and defaults ambiguous queries to
`"current"` — causing stale-penalty misapplication on genuinely timeless queries.

The fourth state, `"unknown"`, is the key addition: it tells Stages 4 and 6 not
to apply aggressive stale penalties when the query has no temporal signal.

## Scope

- `infer_temporality()` function in `query_parser.py`
- `HISTORICAL_INDICATORS` and `CURRENT_INDICATORS` constants
- `trust.query_temporality()` deprecated (not deleted yet; see migration note)

## Components

```
src/second_brain/query_parser.py     — infer_temporality(), HISTORICAL_INDICATORS,
                                       CURRENT_INDICATORS
src/second_brain/trust.py            — query_temporality() marked deprecated;
                                       calls inside retrieval.py redirected to
                                       parse_query() path in S1-INT
```

## Four Temporality States

| State | Conditions | Downstream effect |
|-------|------------|-------------------|
| `"current"` | Current indicators present, no historical | Stage 4 applies full stale penalty; Stage 6 requires fresh notes |
| `"historical"` | Historical indicators present, no current | Stage 4 applies historical relief; Stage 6 accepts stale notes |
| `"mixed"` | Both historical and current indicators present | Stage 4 applies partial penalty; Stage 6 uses `reuse_with_verification` |
| `"unknown"` | Neither indicator present | Stage 4 applies no stale penalty; Stage 6 uses softer assessment |

The shift from legacy `"current"` default to `"unknown"` default means that
queries like "deploy the auth service" (no time words at all) no longer penalize
freshness-stale procedure notes — which is correct, since procedures are often
timeless.

## Constants

```python
HISTORICAL_INDICATORS = frozenset({
    "before", "previously", "historical", "history", "used", "earlier",
    "then", "prior", "ago", "old", "former", "originally", "past",
    "when", "legacy", "was", "were", "had",
})

CURRENT_INDICATORS = frozenset({
    "current", "now", "today", "latest", "currently", "still",
    "active", "live", "present", "existing",
})
```

Indicators are matched against the lowercased token list from Phase 1, not the
raw query string, so stopword filtering and normalization apply.

## Algorithm

```python
def infer_temporality(
    tokens: list[str],
    override: str | None,
) -> str:
    """
    Returns one of: "current", "historical", "mixed", "unknown".

    If override is a valid state string, return it immediately.
    Otherwise infer from tokens.
    """
    valid_states = {"current", "historical", "mixed", "unknown"}
    if override in valid_states:
        return override

    token_set = set(tokens)
    historical = bool(token_set & HISTORICAL_INDICATORS)
    current = bool(token_set & CURRENT_INDICATORS)

    if historical and current:
        return "mixed"
    if historical:
        return "historical"
    if current:
        return "current"
    return "unknown"
```

### Override behavior

`override` comes from `QueryHints.temporality_override`. A valid override bypasses
inference entirely — useful for callers who have out-of-band knowledge about the
query's time focus (e.g. a tool that knows it is running a historical audit).

Invalid override values (not in `valid_states`) are silently ignored and inference
proceeds normally. This prevents callers from injecting arbitrary state strings.

### Wildcard sentinel interaction

If `tokens == ["*"]` (empty query), no indicators match, and the result is
`"unknown"`. This is the correct behavior — a wildcard query has no temporal focus.

## Migration from trust.py

`trust.query_temporality()` (lines 72–78) is called directly in `retrieval.py` at
scoring time. After S1-INT:
1. `retrieval.py` uses `parsed_query.temporality` instead of calling
   `trust.query_temporality()`
2. `trust.query_temporality()` is marked deprecated with a docstring comment
3. The function is retained for compatibility; it is not deleted until any external
   callers are confirmed absent

`CURRENT_QUERY_HINTS` and `HISTORICAL_QUERY_HINTS` in `trust.py` (lines 12–30)
are superseded by `CURRENT_INDICATORS` and `HISTORICAL_INDICATORS` in
`query_parser.py`. The `trust.py` constants are retained until the function that
uses them is removed.

## Failure Policy

`infer_temporality()` never raises. Any list of strings is accepted for `tokens`.
Any string (or None) is accepted for `override`. Invalid override falls through
to inference.

## Verification

- `infer_temporality(["historical", "deploy"], None)` → `"historical"`
- `infer_temporality(["current", "state"], None)` → `"current"`
- `infer_temporality(["before", "now"], None)` → `"mixed"`
- `infer_temporality(["deploy", "auth"], None)` → `"unknown"`
- `infer_temporality(["anything"], "historical")` → `"historical"`
- `infer_temporality(["anything"], "mixed")` → `"mixed"`
- `infer_temporality(["anything"], "invalid")` → `"unknown"` (invalid override ignored)
- `infer_temporality(["*"], None)` → `"unknown"` (wildcard)
- `infer_temporality([], None)` → `"unknown"`
- `infer_temporality(["was", "now"], None)` → `"mixed"` (both historical and current)
- Legacy behavior change: `infer_temporality(["deploy", "auth"], None)` → `"unknown"`
  (legacy `trust.query_temporality()` returned `"current"` for the same input)

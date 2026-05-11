# East Flange Phase 7: Term Expansion

## Goal

Add synonym and alias-derived terms so Stage 2 BM25 and semantic channels can
match notes that use different vocabulary for the same concept. Expansion is
strictly additive — original tokens are never modified or removed.

Cap of 3 additional terms prevents recall explosion on broad queries.

## Scope

- `expand_terms()` function in `query_parser.py`
- `SYNONYM_MAP` constant
- No additional DB round-trips beyond Phase 6 (alias titles come from the Phase 6
  result, not a new query)
- `connection=None` degraded path: expansion from synonym map only

## Components

```
src/second_brain/query_parser.py     — expand_terms(), SYNONYM_MAP
```

## SYNONYM_MAP

Domain-specific synonym map. Covers the most common vocabulary divergence between
queries and note content in MnemonicOS.

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

Synonyms are directional. `"deploy"` expands to `["release", "rollout", "publish"]`
but `"rollout"` does not expand to `"deploy"` unless added explicitly.

### Extending SYNONYM_MAP

Add entries as new domain vocabulary is encountered. Guidelines:
- Prefer concrete domain terms over generic words
- Keep synonym lists to ≤ 4 entries per key
- Do not add stopwords or tokens shorter than 3 characters as synonyms
- Do not create circular expansions that would exceed the 3-term cap

## Algorithm

```python
def expand_terms(
    tokens: list[str],
    anchor_terms: list[str],
    alias_titles: dict[str, str],  # anchor_term → alias title string (from DB)
) -> list[str]:
    """
    Returns up to 3 additional terms not already in tokens.

    alias_titles maps anchor_term → the title string of the matched alias.
    When connection=None (degraded path), alias_titles={} and expansion is
    synonym-map only.
    """
    original_set = set(tokens)
    candidates: list[str] = []
    seen: set[str] = set(tokens)

    # Step 1: synonym expansion from SYNONYM_MAP
    for token in tokens:
        for synonym in SYNONYM_MAP.get(token, []):
            if synonym not in seen:
                candidates.append(synonym)
                seen.add(synonym)
            if len(candidates) >= 3:
                return candidates[:3]

    # Step 2: alias title token expansion
    # For each anchor term that has a known alias title, tokenize the title
    # and add non-stopword, non-duplicate title tokens
    for anchor, title in alias_titles.items():
        title_tokens = [
            t.casefold()
            for t in TOKEN_PATTERN.findall(title)
            if len(t) >= 3 and t.casefold() not in STOPWORDS
        ]
        for tt in title_tokens:
            if tt not in seen:
                candidates.append(tt)
                seen.add(tt)
            if len(candidates) >= 3:
                return candidates[:3]

    return candidates[:3]
```

### alias_titles population

`alias_titles` is built by `parse_query()` during assembly: for each anchor term
returned by Phase 6, look up the alias title from the DB result row (which was
already fetched in Phase 6). No additional DB queries are needed.

If Phase 6 returned `[]` (degraded path or no matches), `alias_titles={}` and
expansion is synonym-only.

### Cap enforcement

`expanded_terms` has a hard cap of 3. Terms are added in this priority order:
1. Synonyms of the first matching token in `tokens` (in `SYNONYM_MAP` key order)
2. Synonyms of subsequent tokens
3. Alias title tokens (lower priority than synonyms)

### Interaction with anchor terms

Anchor terms themselves are not added to `expanded_terms` — they go in
`anchor_terms`. But synonyms of anchor term tokens may appear in `expanded_terms`
if not already in `tokens`.

## Degraded Path (connection=None)

When `parse_query()` runs without a DB connection:
- `anchor_terms = []` (Phase 6 skipped)
- `alias_titles = {}` (no anchor lookups to expand)
- `expanded_terms` = synonym map expansion only (Step 1 still runs)

This is the correct behavior — partial expansion is better than none.

## Failure Policy

`expand_terms()` never raises. Empty `tokens` and empty `SYNONYM_MAP` produce
`[]`. Non-string elements are a caller contract violation and not handled.

## Verification

- `expand_terms(["deploy"], [], {})` → `["release", "rollout"]` (2 synonyms, under cap)
- `expand_terms(["deploy", "incident", "decision"], [], {})` → at most 3 terms total
- Expanded terms do not duplicate anything in `tokens`
- `expand_terms(["unknown_token"], [], {})` → `[]` (no synonym match)
- `expand_terms(["deploy"], ["concept/caching"], {"concept/caching": "Caching Strategy"})` →
  `["release", "rollout"]` (synonym cap reached; alias title not needed)
- `expand_terms(["xyz"], ["concept/caching"], {"concept/caching": "Caching Strategy"})` →
  `["caching", "strategy"]` (synonym miss; alias title fills cap)
- `expand_terms(["deploy", "deploy"], [], {})` → dedup handled by `tokens`; no duplicate synonyms
- Cap of 3 is never exceeded regardless of input size

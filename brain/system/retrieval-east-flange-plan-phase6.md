# East Flange Phase 6: Anchor Term Extraction

## Goal

Identify tokens that are high-confidence candidates for exact-match lookup in
Stage 2. Anchor terms bypass BM25/semantic scoring in Stage 2 — they signal
that the query contains a known identifier (note ID prefix, alias, or exact
title) that should be retrieved directly.

This is Stage 1's single permitted DB read.

## Scope

- `extract_anchor_terms()` function in `query_parser.py`
- `NOTE_ID_PREFIXES` constant
- One alias-table read: `SELECT alias FROM aliases WHERE lower(alias) = lower(?)`
- `connection=None` degraded path: returns `[]`, no exception

## Components

```
src/second_brain/query_parser.py     — extract_anchor_terms(), NOTE_ID_PREFIXES
```

DB table accessed (read-only):
```sql
aliases (alias TEXT, note_id TEXT)
```

## Note ID Prefix Detection

Note IDs follow the convention `<type>/<slug>`. A token matching this pattern is
a strong signal that the caller knows a specific note ID.

```python
NOTE_ID_PREFIXES = frozenset({
    "procedure", "decision", "concept", "incident",
    "person", "project", "repo", "source", "journal", "overview",
})
```

A token is an anchor if it matches `<prefix>/<anything>` where `<prefix>` is in
`NOTE_ID_PREFIXES`. The full token (including the slug) becomes the anchor term.

Examples:
- `"procedure/deploy-auth"` → anchor (whole token)
- `"concept/caching-strategy"` → anchor
- `"procedure"` alone → not an anchor (no slash)

## Alias Table Lookup

For tokens with `len(token) ≥ 4`, check if `lower(token)` appears in
`aliases.alias` (case-insensitive match). Tokens shorter than 4 characters are
skipped to avoid spurious matches on common abbreviations.

To bound query latency, only the 5 longest tokens per query are checked against
the alias table. Tokens that are already anchor terms (matched by note ID prefix)
are excluded from the alias lookup count.

## Algorithm

```python
def extract_anchor_terms(
    tokens: list[str],
    connection: sqlite3.Connection | None,
) -> list[str]:
    """
    Returns deduplicated anchor terms, max 5 entries.

    If connection is None, returns [] without error.
    """
    if connection is None:
        return []

    anchors: list[str] = []
    seen: set[str] = set()

    # Step 1: note ID prefix matches (no DB)
    for token in tokens:
        if "/" in token:
            prefix, _, _ = token.partition("/")
            if prefix in NOTE_ID_PREFIXES and token not in seen:
                anchors.append(token)
                seen.add(token)
                if len(anchors) >= 5:
                    return anchors

    # Step 2: alias table lookup on up to 5 longest eligible tokens
    eligible = sorted(
        (t for t in tokens if len(t) >= 4 and t not in seen),
        key=len,
        reverse=True,
    )[:5]

    for token in eligible:
        if len(anchors) >= 5:
            break
        row = connection.execute(
            "SELECT alias FROM aliases WHERE lower(alias) = lower(?) LIMIT 1",
            (token,),
        ).fetchone()
        if row and token not in seen:
            anchors.append(token)
            seen.add(token)

    return anchors
```

### Latency bound

At most 5 alias-table queries per call (one per eligible token). Each is a
point lookup on a lowered alias value. With an index on `lower(aliases.alias)`,
each lookup is O(log n).

### connection=None path

When `connection=None`, the function returns `[]` immediately. This is the
degraded path: `parse_query()` can run without a DB connection, but anchor
extraction is skipped. `expanded_terms` then comes from the synonym map only
(Phase 7 handles this correctly).

## Failure Policy

`extract_anchor_terms()` never raises on valid inputs. A DB error (e.g. corrupt
alias table) should propagate as an exception — the caller (`parse_query()`)
wraps it in a try/except and returns `anchor_terms=[]` on failure, logging the
error.

```python
# in parse_query() assembly
try:
    anchor_terms = extract_anchor_terms(tokens, connection)
except Exception:
    logger.warning("anchor extraction failed; continuing with empty anchors")
    anchor_terms = []
```

## Verification

- `extract_anchor_terms(["procedure/deploy-auth"], db)` → `["procedure/deploy-auth"]`
- `extract_anchor_terms(["concept/caching"], db)` → `["concept/caching"]`
- `extract_anchor_terms(["procedure"], db)` → `[]` (no slash)
- `extract_anchor_terms(["foo", "bar"], db)` → `[]` (neither in aliases)
- `extract_anchor_terms(["foo"], None)` → `[]` (degraded path)
- Returns at most 5 entries even when more anchors are found
- Short tokens (`len < 4`) are not alias-checked: `extract_anchor_terms(["foo"], db)` → `[]`
- Note ID prefix tokens are not alias-checked (counted in the anchor list but not in the 5-token alias budget)
- Duplicate tokens produce deduplicated anchor list

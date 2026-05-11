# East Flange Phase 1: ParsedQuery Contract and Tokenizer

## Goal

Define all Stage 1 dataclasses and implement the tokenizer that produces
`ParsedQuery.tokens`. This phase establishes the typed contract that every
subsequent phase populates and that all downstream stages consume.

## Scope

Deliverables in this phase:

- `ParsedQuery` frozen dataclass — complete field set including new `caller_type`,
  `persona`, and `back_feed_hook` fields added by this plan
- `QueryHints` dataclass — caller-supplied hints, including new `caller_type_hint`
- `QueryScope` dataclass — workspace scoping hints
- `tokenize()` function
- `models.py` update: `caller_type` field on `ResolveTaskContextRequest`
- Migration of `TOKEN_PATTERN` and `STOPWORDS` from `retrieval.py`

## Components

```
src/second_brain/query_parser.py     — new module; all Phase 1 symbols defined here
src/second_brain/models.py           — add caller_type to ResolveTaskContextRequest
src/second_brain/retrieval.py        — remove TOKEN_PATTERN, STOPWORDS after migration
```

## Data Contracts

### QueryScope

```python
@dataclass(frozen=True, slots=True)
class QueryScope:
    workspace_path: str | None = None   # absolute path; restricts search to workspace
    repo: str | None = None             # repo name filter
    tags: list[str] = field(default_factory=list)
```

### QueryHints

```python
@dataclass(slots=True)
class QueryHints:
    query_type_hint: str | None = None          # override semantic type classifier
    temporality_override: str | None = None     # force: current|historical|mixed|unknown
    token_budget: int | None = None             # caller's context window budget
    scope: QueryScope | None = None             # workspace/repo/tag filter
    top_k: int | None = None                    # requested result count (passed through)
    caller_type_hint: str | None = None         # "human"|"agent"; overrides bridge_id detection
```

`caller_type_hint` is accepted but only trusted when set by an internal path
(e.g. a test harness or a privileged bridge). It is not part of the external API
contract. Persona cannot be overridden via hints.

### ParsedQuery

```python
@dataclass(frozen=True, slots=True)
class ParsedQuery:
    # --- core ---
    raw: str                        # original query string, unmodified
    tokens: list[str]               # lowercased, stopword-filtered, deduplicated
    # --- persona layer (new) ---
    caller_type: str                # "human" | "agent"
    persona: str                    # "knowledge" | "skill" | "rag"
    back_feed_hook: str             # "none" | "skill_outcome" | "rag_synthesis"
    # --- semantic type layer ---
    query_type: str | None          # one of six types; None if unclassifiable
    classifier_confidence: float    # [0.0, 1.0]; 1.0 if from hint
    # --- temporality ---
    temporality: str                # "current"|"historical"|"mixed"|"unknown"
    # --- term layers ---
    anchor_terms: list[str]         # tokens matching aliases or note ID prefixes
    expanded_terms: list[str]       # synonym + alias-title additions
    # --- pass-through ---
    scope: QueryScope | None        # passed through from hints
    token_budget: int | None        # passed through from hints
    top_k: int                      # resolved: hints.top_k or config default
```

`frozen=True` means any attempt to assign a field after construction raises
`FrozenInstanceError`. No downstream stage may hold a mutable reference.

### ResolveTaskContextRequest (models.py addition)

Add one field to the existing dataclass:

```python
caller_type: str | None = None   # explicit caller declaration; threaded into parse_query
```

This is optional and defaults to `None`. When set, it is passed as
`hints.caller_type_hint` in the `QueryHints` constructed by the runtime layer.

## Tokenizer Algorithm

```python
TOKEN_PATTERN = re.compile(r"[\w/-]+", re.UNICODE)

STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do",
    "for", "from", "has", "have", "how", "i", "in", "is", "it",
    "its", "me", "my", "of", "on", "or", "our", "that", "the",
    "to", "us", "was", "we", "what", "which", "who", "with",
})

def tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in TOKEN_PATTERN.findall(query):
        lowered = raw_token.casefold()
        if len(lowered) < 3:
            continue
        if lowered in STOPWORDS:
            continue
        if lowered in seen:
            continue
        tokens.append(lowered)
        seen.add(lowered)
    return tokens if tokens else ["*"]
```

Rules:
1. Extract tokens matching `[\w/-]+` (Unicode-aware; hyphens and slashes kept)
2. Lowercase all tokens
3. Remove tokens shorter than 3 characters
4. Remove stopwords
5. Deduplicate, preserving first-occurrence order
6. If token list is empty after filtering, return `["*"]` (wildcard sentinel)

The wildcard sentinel is never added to `seen`; it signals "no term constraint"
to downstream stages.

## Migration from retrieval.py

`TOKEN_PATTERN` and `STOPWORDS` in `retrieval.py` (lines 26–53) are replaced by
imports from `query_parser`:

```python
from .query_parser import TOKEN_PATTERN, STOPWORDS
```

After the integration phase (S1-INT) completes and `_query_terms()` in
`retrieval.py` is updated to use the shared tokenizer, the local definitions are
deleted. The migration must happen atomically with S1-INT to avoid a window where
both definitions coexist.

STOPWORDS in `query_parser.py` expands the current `retrieval.py` set to include:
`did`, `do`, `has`, `have`, `its`, `me`, `my`, `our`, `us`, `was`, `with`
(11 additions). This is a recall improvement — fewer stopword tokens leak into
BM25 queries.

## Failure Policy

`tokenize()` never raises. Inputs that produce no tokens after filtering return
`["*"]`. Non-string input is a caller contract violation and is not handled.

## Verification

- `tokenize("how do we deploy the auth service")` → `["deploy", "auth", "service"]`
- `tokenize("")` → `["*"]`
- `tokenize("who owns it")` → `["owns"]`
- `tokenize("a an and the")` → `["*"]`
- `tokenize("Ünïcödé tëst")` → `["ünïcödé", "tëst"]`
- `tokenize("deploy deploy deploy")` → `["deploy"]` (dedup)
- `tokenize("us")` → `["*"]` (< 3 chars + stopword)
- `ParsedQuery(raw="x", tokens=["x"], ...)` followed by `pq.raw = "y"` → raises `FrozenInstanceError`
- `QueryHints()` constructs with all defaults `None` without error
- `ResolveTaskContextRequest` accepts `caller_type=None` and `caller_type="agent"` without error

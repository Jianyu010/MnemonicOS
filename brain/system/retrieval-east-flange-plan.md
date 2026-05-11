# Retrieval: East Flange Plan — Stage 1: Query Parsing

## Purpose

The east flange is the retrieval path's query intake boundary. It accepts a raw
natural-language query plus optional caller hints and emits a typed, frozen
`ParsedQuery` struct that all downstream retrieval stages consume without
modification.

"East flange" refers specifically to this stage. Stages 2–7 are internal
retrieval components, not east flange. The symmetric term on the ingestion side
is "west flange."

This governing file defines scope, vocabulary, invariants, and phase order.
Implementation details live in the phase files:

1. [Phase 1: ParsedQuery Contract and Tokenizer](retrieval-east-flange-plan-phase1.md)
2. [Phase 2: Caller Identity Detection](retrieval-east-flange-plan-phase2.md)
3. [Phase 3: Persona Classifier](retrieval-east-flange-plan-phase3.md)
4. [Phase 4: Semantic Type Classifier](retrieval-east-flange-plan-phase4.md)
5. [Phase 5: Temporality Inference](retrieval-east-flange-plan-phase5.md)
6. [Phase 6: Anchor Term Extraction](retrieval-east-flange-plan-phase6.md)
7. [Phase 7: Term Expansion](retrieval-east-flange-plan-phase7.md)
8. [Integration: parse_query() Assembly](retrieval-east-flange-plan-int.md)

---

## Glossary

- **East flange**: Stage 1 of the retrieval pipeline — the query intake boundary.
- **ParsedQuery**: typed, frozen dataclass emitted by Stage 1. Sole input to Stage 2.
- **QueryHints**: optional caller-supplied metadata that supplements or overrides
  inference. Accepted but never trusted blindly.
- **Persona**: meta-classification capturing who is asking and what output shape
  they need. One of `knowledge`, `skill`, `rag`.
- **Query type**: semantic classification capturing what is being asked. One of
  six fixed values. Orthogonal to persona.
- **Caller type**: detected identity of the query source. One of `human`, `agent`.
- **Back-feed hook**: signal that Stage 7 reads to decide which feedback path to
  activate after the query result is consumed.
- **Anchor term**: token matching a known alias, note ID prefix, or exact title.
  Used by Stage 2 exact channel.
- **Expanded term**: synonym or alias-title token not in the original query, added
  to increase BM25/semantic recall.
- **Temporality**: inferred time focus of the query — one of `current`,
  `historical`, `mixed`, `unknown`.
- **Wildcard sentinel**: the token `"*"` returned by the tokenizer when the input
  produces no meaningful tokens after filtering.
- **TYPE_PRIORS**: constant mapping query type to note-type score boosts. Consumed
  by Stage 4 reranking.
- **SYNONYM_MAP**: constant mapping domain tokens to synonyms. Used in Phase 7.
- **Bridge**: a named integration that connects an external agent runtime to
  MnemonicOS. Identified by `bridge_id`.
- **CLI**: command-line interface. `mnemonic_context.sh` is the CLI entry point.
  CLI callers have no `bridge_id`.

---

## Taxonomy Design

Stage 1 classifies every query on two orthogonal dimensions:

### Dimension 1 — Persona

Answers: *who is asking, why, and what output shape do they need?*

| Persona | Caller | Intent | Output format | Back-feed hook |
|---------|--------|--------|---------------|----------------|
| `knowledge` | human | Truth-seeking open question | HTML: title + sections + sources + links | `none` |
| `skill` | agent | Procedural guidance from stored memory | Structured MD: GOAL → PATH → AVOID → FALLBACK | `skill_outcome` |
| `rag` | agent | Broad surfacing before consulting internet | Standard `MemoryPacket` | `rag_synthesis` |

Stage 7 routes output format and back-feed activation on `persona`.

### Dimension 2 — Semantic Type

Answers: *what is the grammatical/semantic class of the question?*

| Type | Keywords | Note-type boost (TYPE_PRIORS) |
|------|----------|-------------------------------|
| `preference_identity` | prefer, preference, style, identity | person 0.9, concept 0.5 |
| `how_to` | how, steps, deploy, run, procedure, release | procedure 0.9, repo 0.6, project 0.4 |
| `decision` | why, decision, decide, choose, picked, rationale | decision 0.9, incident 0.6, project 0.4 |
| `who_where_relation` | who, owner, owns, responsible, maintainer | person 0.9, project 0.7, repo 0.5 |
| `what_changed` | changed, recent, latest, new, update | incident 0.8, project 0.7, decision 0.6 |
| `broad_synthesis` | state, summary, overview, status | overview 0.9, project 0.7, concept 0.5 |

Stage 4 consumes type via TYPE_PRIORS. Persona has no Stage 4 effect.

**Interaction rule**: human + `how_to` → persona `knowledge` (human wants a
readable explanation, not terse agent steps). The persona classifier always wins
on output shape; the type classifier always wins on Stage 4 scoring.

---

## Boundary

Stage 1 owns:
- Tokenization of the raw query string
- Detection of caller identity from `bridge_id`
- Classification of query persona (three values)
- Classification of semantic query type (six values)
- Inference of query temporality (four states)
- Extraction of anchor terms via one alias-table read
- Expansion of terms via synonym map and alias titles

Stage 1 does not own:
- BM25 query construction from expanded terms (Stage 2)
- Embedding computation (Stage 2)
- Signal loading — trust, freshness, importance (Stage 3)
- Scoring formula or TYPE_PRIORS application (Stage 4)
- Slot composition or packet assembly (Stage 5)
- Retrieval state assessment (Stage 6)
- Output formatting or HTML rendering (Stage 7)
- Any DB writes

---

## Non-Negotiable Invariants

- `ParsedQuery` is frozen. No downstream stage may mutate it.
- Stage 1 performs at most one DB read (alias table, Phase 6).
- Stage 1 performs no DB writes.
- `parse_query()` is the sole public entry point. No caller may access Phase
  functions directly in production code paths.
- Caller type is inferred from `bridge_id`, not supplied by the caller.
  `caller_type_hint` in `QueryHints` may override only when explicitly set by a
  trusted internal path.
- Persona is inferred, never caller-supplied via hints.
- If `connection=None`, anchor extraction is skipped and `anchor_terms=[]`. No
  exception is raised.
- Empty or stopword-only queries return token list `["*"]` (wildcard sentinel).
  Downstream stages handle `"*"` as "no term constraint."

---

## Module

All Phase 1–7 and integration logic lives in one new module:

```
src/second_brain/query_parser.py
```

**Exported symbols:**
```python
# Dataclasses
ParsedQuery
QueryHints
QueryScope

# Enums / constants
CallerType        # "human" | "agent"
PersonaType       # "knowledge" | "skill" | "rag"
TemporalityType   # "current" | "historical" | "mixed" | "unknown"
TYPE_PRIORS       # dict[str, dict[str, float]] — consumed by Stage 4
SYNONYM_MAP       # dict[str, list[str]] — used in Phase 7

# Public API
parse_query(raw, hints, *, config, connection, bridge_id) -> ParsedQuery

# Utilities (exposed for unit testing only)
tokenize(query: str) -> list[str]
detect_caller(bridge_id: str | None, hint: str | None) -> str
classify_persona(caller_type: str, query_type: str | None, tokens: set[str]) -> str
classify_query(tokens: list[str], hint: str | None) -> tuple[str | None, float]
infer_temporality(tokens: list[str], override: str | None) -> str
extract_anchor_terms(tokens: list[str], connection: sqlite3.Connection | None) -> list[str]
expand_terms(tokens: list[str], anchor_terms: list[str], alias_titles: dict[str, str]) -> list[str]
```

---

## Legacy State

Existing partial implementations to migrate and retire:

| Legacy location | Phase | Action |
|----------------|-------|--------|
| `retrieval.py:_classify_query()` lines 126–140 | Phase 4 | Migrate to `query_parser.py`, delete original |
| `retrieval.py:TYPE_PRIORS` lines 64–71 | Phase 4 | Move to `query_parser.py`, import from there |
| `retrieval.py:QUERY_HINTS` lines 55–62 | Phase 4 | Expand and move to `query_parser.py` |
| `retrieval.py:STOPWORDS` lines 27–53 | Phase 1 | Move to `query_parser.py` |
| `retrieval.py:TOKEN_PATTERN` line 26 | Phase 1 | Move to `query_parser.py` |
| `trust.py:query_temporality()` lines 72–78 | Phase 5 | Deprecated; `parse_query()` governs |
| `trust.py:CURRENT_QUERY_HINTS` lines 12–20 | Phase 5 | Superseded by `CURRENT_INDICATORS` in Phase 5 |
| `trust.py:HISTORICAL_QUERY_HINTS` lines 22–30 | Phase 5 | Superseded by `HISTORICAL_INDICATORS` in Phase 5 |

---

## Completion Gate

Stage 1 is complete when:

1. `parse_query()` is the sole query-parsing entry point in all production code
   paths — `_classify_query()` removed from `retrieval.py`
2. `ParsedQuery` carries `caller_type`, `persona`, `back_feed_hook` alongside all
   prior planned fields
3. All per-phase unit tests pass (see each phase file's Verification section)
4. `ParsedQuery` is accepted by Stage 2 `recall()` in an end-to-end integration
   test against a seeded in-memory SQLite database
5. `trust.query_temporality()` is deprecated as the canonical temporality path
6. No regression on `evals/queries.jsonl` top-1 recall vs. the legacy baseline

---

## Reference

**Roadmap:** [retrieval-east-flange-roadmap.md](retrieval-east-flange-roadmap.md)
**Overall retrieval roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
**Audit baseline:** [docs/east-flange-audit.md](../../docs/east-flange-audit.md)

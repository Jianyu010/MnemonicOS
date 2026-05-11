# East Flange Phase 3: Persona Classifier

## Goal

Classify every query into one of three personas — `knowledge`, `skill`, or `rag`
— based on the detected caller type and the semantic query type from Phase 4.
The persona determines output format (Stage 7) and back-feed hook.

## Scope

- `classify_persona()` function in `query_parser.py`
- `RAG_SIGNALS` constant
- `BACK_FEED_HOOKS` table (maps persona → hook string)
- No DB access; pure computation over caller_type + query_type + token set

## Persona Definitions

| Persona | Caller | Output format | Back-feed hook | Semantic types |
|---------|--------|---------------|----------------|----------------|
| `knowledge` | `human` (always) | HTML: title, sections, sources, links | `none` | any |
| `skill` | `agent` | Structured MD: GOAL→PATH→AVOID→FALLBACK | `skill_outcome` | `how_to` |
| `rag` | `agent` | Standard `MemoryPacket` | `rag_synthesis` | all others |

## Components

```
src/second_brain/query_parser.py     — classify_persona(), RAG_SIGNALS, BACK_FEED_HOOKS
```

## Algorithm

```python
RAG_SIGNALS = frozenset({
    "know", "surface", "research", "background",
    "context", "explore", "everything", "about",
})

BACK_FEED_HOOKS: dict[str, str] = {
    "knowledge": "none",
    "skill":     "skill_outcome",
    "rag":       "rag_synthesis",
}

def classify_persona(
    caller_type: str,
    query_type: str | None,
    tokens: set[str],
) -> str:
    """
    Returns "knowledge", "skill", or "rag".

    Rules applied in order:
    1. Human caller → always "knowledge" (readable output over terse output)
    2. Agent + how_to type → "skill" (procedure from memory)
    3. Agent + broad_synthesis type, or RAG_SIGNALS in tokens → "rag"
    4. Agent + any other type or None → "rag" (safe broad default)
    """
    if caller_type == "human":
        return "knowledge"
    # agent from here
    if query_type == "how_to":
        return "skill"
    if query_type == "broad_synthesis" or tokens & RAG_SIGNALS:
        return "rag"
    return "rag"
```

### Edge case table

| Caller | Semantic type | Tokens | Persona | Rationale |
|--------|---------------|--------|---------|-----------|
| `human` | `how_to` | any | `knowledge` | Human wants explanation, not terse agent steps |
| `human` | `decision` | any | `knowledge` | Human wants context and reasoning |
| `human` | `None` | any | `knowledge` | Unclassifiable human query still gets rich output |
| `agent` | `how_to` | any | `skill` | Agent needs GOAL/PATH/AVOID structure |
| `agent` | `broad_synthesis` | any | `rag` | Agent surfacing stored knowledge broadly |
| `agent` | `decision` | any | `rag` | Agent gathering context before deciding |
| `agent` | `preference_identity` | any | `rag` | Preference context → broad surface |
| `agent` | `who_where_relation` | any | `rag` | Relational context → broad surface |
| `agent` | `what_changed` | any | `rag` | Diff/recency context → broad surface |
| `agent` | `None` | `{"research"}` | `rag` | RAG signal in tokens overrides unclassified |
| `agent` | `None` | any | `rag` | Unclassifiable agent query defaults to broad |

### Extending the persona classifier

To add a new persona in the future:
1. Add the string value to `PersonaType` enum (if formalized)
2. Add a rule before the final `return "rag"` fallback
3. Add the back-feed hook to `BACK_FEED_HOOKS`
4. Update Stage 7 to handle the new persona

Do not change existing rules without updating the edge case table and all
downstream stage handlers.

## Back-Feed Hook Table

```python
BACK_FEED_HOOKS = {
    "knowledge": "none",
    "skill":     "skill_outcome",
    "rag":       "rag_synthesis",
}
```

`back_feed_hook` is set in the `parse_query()` assembly step (S1-INT) using
this table: `back_feed_hook = BACK_FEED_HOOKS[persona]`.

Stage 7 behavior per hook:
- `"none"`: no automatic back-feed; human satisfaction is measured by follow-up
  queries, not outcome labeling
- `"skill_outcome"`: on task completion, call `record_outcome()` with
  `task_kind="skill"`; if `retrieval_hits.useful` sample count ≥ threshold,
  trigger `train_rerank_model()`
- `"rag_synthesis"`: on agent writeback, capture synthesis connections as new
  notes or graph edges; call `record_outcome()` with `task_kind="knowledge"`

## Output Format Contracts (defined here; rendered in Stage 7)

### knowledge → HTML

```python
@dataclass
class KnowledgeOutputSchema:
    title: str
    summary: str                          # 1-2 sentence answer lead
    sections: list[KnowledgeSection]
    sources: list[SourceRef]              # note_id, title, freshness_state
    links: list[str]                      # URLs found in note bodies
    persona: str = "knowledge"

@dataclass
class KnowledgeSection:
    heading: str
    body: str                             # Markdown; Stage 7 renders to HTML
    section_type: str                     # "explanation"|"example"|"caveat"|"diagram_link"

@dataclass
class SourceRef:
    note_id: str
    title: str
    freshness_state: str | None
```

Stage 7 checks `parsed_query.persona == "knowledge"` and routes to the HTML
renderer. Plain Markdown is the acceptable fallback if HTML rendering is not
yet implemented in Stage 7.

### skill → structured Markdown string

Canonical template (rendered as plain text; JSON wrapper acceptable for API):

```
## GOAL
<what the agent is trying to accomplish>

## SUCCESSFUL PATH
1. <step>
2. <step>
...

## AVOID
- <anti-pattern or forbidden approach>

## FALLBACK (if <condition>)
<alternative path>

## SOURCES
- <note title> [<freshness_state>] (<note_id>)
```

### rag → standard MemoryPacket

No format changes. Existing `primary_memories`, `related_*`, `archive_context`,
`contradictions`, `warnings` slots unchanged. Stage 7 existing Markdown/JSON paths.

## Failure Policy

`classify_persona()` never raises. The return value is always one of three
valid strings. No DB access. No I/O.

## Verification

- `classify_persona("human", "how_to", set())` → `"knowledge"`
- `classify_persona("human", None, set())` → `"knowledge"`
- `classify_persona("human", "decision", {"why", "choose"})` → `"knowledge"`
- `classify_persona("agent", "how_to", set())` → `"skill"`
- `classify_persona("agent", "how_to", {"research"})` → `"skill"` (type wins over RAG_SIGNALS)
- `classify_persona("agent", "broad_synthesis", set())` → `"rag"`
- `classify_persona("agent", "decision", set())` → `"rag"`
- `classify_persona("agent", "preference_identity", set())` → `"rag"`
- `classify_persona("agent", None, {"research"})` → `"rag"`
- `classify_persona("agent", None, set())` → `"rag"`
- `BACK_FEED_HOOKS["knowledge"]` → `"none"`
- `BACK_FEED_HOOKS["skill"]` → `"skill_outcome"`
- `BACK_FEED_HOOKS["rag"]` → `"rag_synthesis"`

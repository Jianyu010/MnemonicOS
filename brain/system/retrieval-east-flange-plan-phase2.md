# East Flange Phase 2: Caller Identity Detection

## Goal

Detect whether the query originates from a human or an agent, using `bridge_id`
as the primary signal. The result (`caller_type`) drives the persona classifier
in Phase 3.

## Scope

- `detect_caller()` function in `query_parser.py`
- `AGENT_BRIDGE_IDS` constant
- No changes to CLI scripts or bridge adapters — CLI already produces no `bridge_id`

## Rationale

Three fundamentally different consumers query MnemonicOS:

1. **Humans** via `mnemonic_context.sh` — want rich, readable, sourced output
2. **Known agent bridges** (`claude`, `codex`) — want terse, machine-parseable output
3. **Generic programmatic callers** (any `bridge/` or `api/` prefix) — treated as agents

Caller identity is inferred, not trusted from the query body. The only
authoritative override path is `QueryHints.caller_type_hint`, which is for
internal use (test harnesses, trusted bridges that explicitly declare identity).

## Components

```
src/second_brain/query_parser.py     — detect_caller(), AGENT_BRIDGE_IDS
```

No other files change in this phase.

## Algorithm

```python
AGENT_BRIDGE_IDS = frozenset({"claude", "codex"})

def detect_caller(bridge_id: str | None, hint: str | None) -> str:
    """
    Returns "human" or "agent".

    Priority:
    1. Explicit hint (caller_type_hint from QueryHints) — wins unconditionally
    2. bridge_id absence → human (CLI path)
    3. bridge_id in known agent set → agent
    4. bridge_id starts with programmatic prefix → agent
    5. Unknown bridge_id → human (safe default: give readable output over terse output)
    """
    if hint in {"human", "agent"}:
        return hint
    if not bridge_id:
        return "human"
    lower_id = bridge_id.casefold()
    if lower_id in AGENT_BRIDGE_IDS:
        return "agent"
    if bridge_id.startswith(("bridge/", "api/")):
        return "agent"
    return "human"
```

### CLI behavior

`mnemonic_context.sh` calls the Python retrieval layer without setting
`bridge_id`. The existing call chain passes `bridge_id=""` or omits it entirely.
Both `None` and `""` evaluate as falsy in `not bridge_id`, so CLI always resolves
to `"human"`. No changes to the script are needed.

### Safe-default rationale

Unknown `bridge_id` values default to `"human"` (persona = `knowledge` → richer
output) rather than `"agent"` (persona = `skill`/`rag` → terse output). Over-
informing an agent is less harmful than under-informing a human. New agent bridges
are added to `AGENT_BRIDGE_IDS` when integrated.

### Adding new agent bridges

When a new bridge is integrated:
1. Add its `bridge_id` string to `AGENT_BRIDGE_IDS` in `query_parser.py`
2. No other code changes needed
3. If the bridge uses a `bridge/` or `api/` prefix, it is already covered

## Failure Policy

`detect_caller()` never raises. Any value of `bridge_id` (including unexpected
types that compare falsy) resolves to a valid return value. Non-string `hint`
values that are not in `{"human", "agent"}` are ignored (fall through to
`bridge_id` detection).

## Verification

- `detect_caller(None, None)` → `"human"`
- `detect_caller("", None)` → `"human"`
- `detect_caller("claude", None)` → `"agent"`
- `detect_caller("CLAUDE", None)` → `"agent"` (case-insensitive)
- `detect_caller("codex", None)` → `"agent"`
- `detect_caller("bridge/my-bot", None)` → `"agent"`
- `detect_caller("api/v1", None)` → `"agent"`
- `detect_caller("unknown-tool", None)` → `"human"`
- `detect_caller("claude", "human")` → `"human"` (hint overrides)
- `detect_caller(None, "agent")` → `"agent"` (hint overrides absence)
- `detect_caller("unknown", "agent")` → `"agent"` (hint overrides detection)

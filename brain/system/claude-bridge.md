# MnemonicOS × Claude Code Bridge

## Status

Three phases fully implemented.

- Phase 1 (shared orchestrator commonplace): done.
- Phase 2 (hook rewiring to call orchestrator CLI): done.
- Phase 3 (skill-based enforcement layer): done — `.claude/skills/mnemonicos-bridge/SKILL.md`
  auto-discovered by CC from project `.claude/skills/` directory (skills-dir mechanism).

The hooks are thin shell wrappers over `mnemonicos orchestrator begin-task` and
`mnemonicos orchestrator finish-task`. They do not chain raw runtime operations
directly. The skill enforces that the agent actually reads and acts on injected context.

---

## How Hooks Fire

Claude Code's hook model fires at lifecycle events, not at individual LLM
reasoning steps. Within one user turn, Claude may make many LLM calls — only
the first gets memory context.

```
UserPromptSubmit (1×) ← begin_task fires HERE — memory injected into prompt
  ↓
[Claude reasons → LLM call #1]
  ├─ PreToolUse / PostToolUse (per tool call, not per reasoning step)
  ├─ [Claude reasons with tool output → LLM call #2, #3 …]
  └─ repeat …
  ↓
Stop (1×)             ← finish_task fires HERE — per-turn writeback
```

Implications:

- Memory context is injected once per user turn.
- `mark_failure` (mid-turn failure re-retrieval) is a manual step per CLAUDE.md.
- There is no mid-session hook, so the adapter cannot hard-gate intermediate LLM
  calls the way a Codex wrapper can at task-submission breakpoints.

---

## Hook Wiring

**`mnemonic_hook_preflight.sh`** (UserPromptSubmit):

```bash
mnemonicos orchestrator begin-task \
  --bridge-id claude \
  --session-id "$SESSION_ID" \
  --task-text "$PROMPT" \
  --json
```

- Writes `task_id` from the response to `/tmp/mnemo_hook_state` for the Stop
  hook to pick up.
- Returns `{"context": "..."}` — Claude Code requires this exact format for
  context injection.
- `SESSION_ID` comes from the hook's own stdin JSON (present in both
  UserPromptSubmit and Stop events).

**`mnemonic_stop_hook.sh`** (Stop):

```bash
mnemonicos orchestrator finish-task \
  --bridge-id claude \
  --task-id "$(cat /tmp/mnemo_hook_state 2>/dev/null)" \
  --summary-file /tmp/mnemo_pending_summary.md
```

- Falls back to `mnemonic_postflight.sh` if state file is absent (backward
  compatibility).
- Stop fires once per user turn (not once at session end). The natural ingestion
  unit is a single user-prompt turn.

**`mnemonic_hook_post_tool.sh`** (PostToolUse, success only — CC 2.1.143+):

Appends `tool_call` and `tool_result` (is_error=false) events to the active
episode. Does NOT emit failure events — those go to the failure hook.

**`mnemonic_hook_post_tool_failure.sh`** (PostToolUseFailure — CC 2.1.143+):

Appends `tool_call`, `tool_result` (is_error=true), and `failure` events.
Input fields: `error`, `error_type`, `is_interrupt`, `is_timeout` (no `tool_response`).

**`mnemonic_hook_file_artifact.sh`** (PostToolUse Write/Edit, success only):

Appends `file_changed` or `artifact_created` events to the active episode,
tracking which files Claude modified during the turn.

**`mark_failure`** (enforced by skill, per `.claude/skills/mnemonicos-bridge/`):

No mid-session hook exists. Failure re-retrieval is triggered by the
`mnemonicos-bridge` skill, which mandates `mnemonic_context.sh` after the
first meaningful failure. If a mid-session hook becomes available later,
`mnemonicos orchestrator mark-failure --bridge-id claude --task-id ...` is
the correct call.

---

## Task ID and Session ID

- `session_id`: stable across all turns in one Claude Code session. Comes from
  Claude Code's hook stdin JSON — no storage needed.
- `task_id`: per-turn. Generated fresh by `begin_task` at UserPromptSubmit.
  Stored in `/tmp/mnemo_hook_state_<sha256(session_id)[:16]>` (one per
  session), read and deleted by the Stop hook.

This means per-turn writeback works naturally: turn 1's summary is ingested
before turn 2 begins. The next turn's retrieval can find the previous turn's
writeback within the same `session_id`.

---

## Writeback Policy

Claude queues a summary with `scripts/mnemonic_pending_summary.sh --agent claude`
after each meaningful turn, not at session end. The helper always creates a new
file under `/tmp/mnemo_pending/`, so Claude and Codex sessions never overwrite
each other. The Stop hook ingests every claude-owned file via `finish_task` (or
`mnemonic_postflight.sh` when no task_id exists) and deletes each one only after
it was accepted. The legacy `/tmp/mnemo_pending_summary.md` is still read when
its `agent:` header is `claude`.

What counts as a meaningful turn:

- Meaningful failure (retry with changed approach, error requiring diagnosis,
  wrong assumption revealed)
- Reusable learning (better command, confirmed or fixed procedure, knowledge
  session)

Trivial turns (`ls`, `pwd`, simple reads, follow-up clarifications) → skip.

If the context window approaches its limit before the Stop hook fires, the
PreCompact hook dumps any pending summary to `brain/raw/sessions/` so it is not
lost on compaction.

---

## Enable / Disable Switch

Both hooks check a two-tier switch at the top of each script before any work.

**Tier 1 — env var (session-scoped):**

```bash
MNEMO_ENABLED=0 claude     # disable for this shell session
```

**Tier 2 — flag file (persistent):**

```bash
touch config/mnemo_disabled   # disable persistently
rm    config/mnemo_disabled   # re-enable
```

When disabled, both hooks `exit 0` immediately — no CLI call, no state file,
no pending summary deletion. Identical to having no hooks registered.

---

## A/B Testing

```bash
# Round A — baseline (MnemonicOS off)
touch config/mnemo_disabled
# run tasks, observe completion quality and speed

# Round B — enabled
rm config/mnemo_disabled
# run same tasks, compare
```

Useful metrics: task completion rate, tool-call count per task, retry/re-prompt
count, subjective quality on known-domain tasks.

---

## Skill Layer

The hook layer is passive — it injects context but cannot enforce that the agent
reads or acts on it. The skill layer closes this gap.

**`.claude/skills/mnemonicos-bridge/SKILL.md`** (project scope, in repo):

Auto-discovered by Claude Code from the `.claude/skills/` directory (CC's
`skills-dir` mechanism). No settings.json registration needed. Shows up in the
available-skills list that superpowers enforces.

Also installed globally at `~/.claude/skills/mnemonicos-bridge/SKILL.md` for
activation from any project that carries MnemonicOS context (global CLAUDE.md).

What the skill enforces:

1. **Read** the injected `## Memory Context` block and act on `retrieval_state`
2. **Run manual retrieval** if the hook missed or state is insufficient
3. **Re-query** after the first meaningful failure (mandatory, not optional)
4. **Dedup-aware writeback**: `confirm > update > new`

The superpowers `using-superpowers` skill checks the available-skills list
before every response. Because `mnemonicos-bridge` is now registered, it fires
automatically on any non-trivial task — not only when the user explicitly invokes it.

---

## Hook Registration

Hooks are registered globally in `~/.claude/settings.json`. No per-project
registration is needed. Claude Desktop uses the same file — no separate config.

Relevant hook scripts:

- `scripts/mnemonic_hook_preflight.sh` (UserPromptSubmit)
- `scripts/mnemonic_hook_post_tool.sh` (PostToolUse — success only)
- `scripts/mnemonic_hook_post_tool_failure.sh` (PostToolUseFailure)
- `scripts/mnemonic_hook_file_artifact.sh` (PostToolUse Write/Edit)
- `scripts/mnemonic_stop_hook.sh` (Stop)
- `scripts/mnemonic_precompact_dump.sh` (PreCompact)

---

## Manual Use

### Retrieve context for a query

```bash
scripts/mnemonic_context.sh "debug the auth deploy failure"
scripts/mnemonic_context.sh "debug the auth deploy failure" --json
```

### Retrieve with a specific query type hint

```bash
scripts/mnemonic_context.sh "how does session ingest work" \
  --query-type-hint how_to
```

### Force retrieval check

```bash
scripts/mnemonic_should_retrieve.sh --task "debug the auth deploy failure"
```

---

## Key Differences From Codex

| | Claude Code | Codex |
|---|---|---|
| Passive injection | Hook (UserPromptSubmit) | Hook (preflight script) |
| Active enforcement | Skill (`.claude/skills/mnemonicos-bridge/`) | Skill (`codex_skills/mnemonicos-bridge/`) |
| Fallback mode | None — hooks + skill always active | Codex app (no adapter) |
| `mark_failure` | Skill-enforced manual re-query | Explicit adapter call |
| Transport | CLI (hooks cannot do Python imports) | Python, CLI, or HTTP |
| Granularity | Once per user turn | Once per top-level task submission |
| Tool failure capture | Separate `PostToolUseFailure` hook (CC 2.1.143+) | Not applicable |

# MnemonicOS × Codex Bridge

## Status

- Phase 1 (shared orchestrator commonplace): done.
- Phase 2 (Codex adaptation — MCP layer, local wrapper, breakpoint checkpointing): planned, not yet implemented.

The shared orchestrator (`begin_task`, `mark_failure`, `finish_task`) is
available via Python, CLI, and HTTP. Codex can call it through any of these,
but the recommended Codex-specific adaptation layer (MCP server, `mnemo-codex`
wrapper, adapter-owned local state) is not yet built.

---

## Architectural Difference From Claude Code

Codex does not have a platform-level hook system. MnemonicOS cannot register
binary-enforced hooks the way it does for Claude Code. The enforcement model
is therefore different:

| | Claude Code | Codex |
|---|---|---|
| Enforcement | Hard-gate via registered hook | Skill-based or wrapper-based (current: skill-based) |
| Fallback | None — hooks always active | Codex app without wrapper = no MnemonicOS |
| `mark_failure` | Manual per CLAUDE.md | Explicit adapter call or tool invocation |
| Transport | CLI only (hook shell scripts) | Python, CLI, or HTTP |
| Granularity | Once per user turn | Once per top-level task submission |

The Codex integration is strongest when Codex is launched through a wrapper
(`mnemo-codex`) that hard-gates MnemonicOS before each top-level task. Without
the wrapper, the current skill-based path is best-effort.

---

## Current Path (Skill-Based, Best-Effort)

Use the same repo-local scripts as Claude Code:

- `scripts/mnemonic_context.sh`
- `scripts/mnemonic_should_retrieve.sh`
- `scripts/mnemonic_should_writeback.sh`
- `scripts/mnemonic_should_upsert.sh`
- `scripts/mnemonic_postflight.sh`

Install or refresh the bridge skill:

```bash
scripts/install_codex_skill.sh
```

After installing or refreshing the skill, start a new Codex session. If using
a custom `CODEX_HOME`, the installer and runtime must use the same value.

**Current caveat:** the Codex skill is script-based, not hook-based. Disabling
MnemonicOS does not fully prevent direct skill/script calls. For Codex testing,
either avoid invoking the skill or run in a workspace without the wired scripts.

### Recommended Codex Workflow

**Preflight:**

```bash
scripts/mnemonic_should_retrieve.sh --task "debug the auth deploy failure"
scripts/mnemonic_context.sh "debug the auth deploy failure" --json
```

Use the `retrieval_state` / `recommendation` pair:

- `sufficient_current` + `reuse`: answer from MnemonicOS first
- `sufficient_historical` + `reuse`: use only for historical questions
- `partial` + `reuse_with_verification`: use as scaffold, then verify
- `insufficient_*` + `research_fresh`: do normal repo/tool/web research

**Post-task writeback:**

```bash
scripts/mnemonic_should_writeback.sh \
  --mode success \
  --summary-file /tmp/mnemo_pending_summary.md

scripts/mnemonic_postflight.sh \
  --agent codex \
  --slug codex-$(date +%Y-%m-%d-%H%M%S) \
  --summary-file /tmp/mnemo_pending_summary.md
```

---

## Planned Phase 2: Codex Adaptation Layer

This section describes the planned (not yet implemented) hard-enforcement path.

### Core Decision

Do not intercept every hidden internal Codex LLM call. Instead:

- hard-gate MnemonicOS at each top-level task boundary via a local wrapper
- encourage reuse during the run via standing Codex instructions and MCP tools
- treat one live Codex CLI run as a `session`
- treat each user-entered objective as a separate `task`

### Consultation Points

**Guaranteed** (wrapper-enforced):

- each new top-level task submitted through `mnemo-codex`
- each new user-entered objective after a breakpoint

**Recommended** (instruction-guided, not hard-gated):

- after the first meaningful failure in a task
- when Codex changes approach materially
- when retrieval state is partial, stale, or contested

### Planned Components

**Codex-facing MCP layer:**

Thin MCP server exposing:

- `mnemos_begin_task` → `begin_task`
- `mnemos_mark_failure` → `mark_failure`
- `mnemos_finish_task` → `finish_task`

All retrieval, outcome logging, and writeback semantics stay inside the shared
orchestrator. The MCP layer is a thin pass-through.

**`mnemo-codex` local wrapper:**

- hard-gate `begin_task` before each top-level task when MnemonicOS is ON
- bypass cleanly when the switch is OFF (same wrapper path, no MnemonicOS calls)
- prepend returned `context_markdown` automatically when ON
- persist adapter-owned local state: `session_id`, `task_id`, `retrieval_state`,
  `recommendation`, switch state

**Breakpoint checkpointing:**

- at each user-input breakpoint, write an archive-style checkpoint (not a full
  canonical note) preserving failures and partial progress
- run full `finish_task` writeback selectively for meaningful success, failure,
  or durable knowledge
- do not flood canonical memory from high-frequency breakpoints

**Evaluation switch:**

- default ON
- controlled via wrapper flag, env var, or adapter session config
- when OFF: same wrapper path, no MnemonicOS calls, adapter-side telemetry
  records that the session ran without MnemonicOS

### Acceptance Criteria (Phase 2)

- wrapper calls `begin_task` before each top-level task when switch is ON
- same top-level tasks forwarded unchanged when switch is OFF
- ON and OFF modes use the same wrapper and state path
- MCP tool calls map directly to orchestrator contracts
- `session_id` and per-task `task_id` persist across one Codex run
- breakpoint checkpointing preserves failures without requiring one giant
  end-of-session summary
- non-wrapper Codex usage is documented as best-effort, not hard guarantee

---

## Identity Model

- `session_id`: one live Codex CLI run or wrapper-owned conversation span.
- `task_id`: one user-entered objective between two user-input breakpoints.
- `bridge_id`: `codex` for all Codex adapters.

Defaults inferred by the orchestrator:

- `task_kind`: inferred from task text (`debugging`, `coding`, `architecture`,
  `research`, else `general`)
- `temporality`: `current` for coding/debugging, `mixed` for
  architecture/research
- `token_budget`: larger default for controlled API callers

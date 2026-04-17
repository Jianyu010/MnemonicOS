# MnemonicOS × Claude Code Integration

## How It Works

MnemonicOS is wired into Claude Code via three hooks and a `CLAUDE.md`:

| Component | Role |
|---|---|
| `UserPromptSubmit` hook | Automatically runs retrieval before Claude reads your prompt |
| `Stop` hook | Automatically ingests the session summary when Claude ends |
| `PreCompact` hook | Dumps the pending summary to `brain/raw/sessions/` before compaction discards context |
| `CLAUDE.md` | Standing instructions: when Claude retrieves again, when and how to write summaries |

Claude Desktop uses the same hooks and settings — no separate config needed.

---

## Retrieval Flow

```
You type a prompt
    ↓
UserPromptSubmit hook fires
    ↓
mnemonic_should_retrieve.sh checks prompt
    ↓ (retrieval warranted)
mnemonic_context.sh runs hybrid retrieval
    ↓
Context block prepended to prompt
    ↓
Claude sees: [Memory Context] + [Your Prompt]
```

Retrieval is skipped for trivial commands (`ls`, `git status`, etc.) and short one-liners.

After the **first meaningful failure** in a task, Claude is also instructed to call retrieval directly before continuing.
If retrieval says the memory is stale, contested, or missing, Claude should use that as a fallback signal and do fresh research instead of blindly trusting memory.

---

## Writeback Flow

```
Task ends (or meaningful event occurs)
    ↓
Claude evaluates: meaningful failure or reusable success?
    ↓ (yes)
Claude writes structured summary to /tmp/mnemo_pending_summary.md
    ↓
         ┌─── context window approaches limit ───┐
         │                                       │
         │  PreCompact hook fires                │
         │  mnemonic_precompact_dump.sh          │
         │  copies pending summary →             │
         │  brain/raw/sessions/TIMESTAMP-        │
         │  claude-precompact-{trigger}.md       │
         │  (skips cleanly if no summary exists) │
         └───────────────────────────────────────┘
    ↓
Session ends → Stop hook fires
    ↓
mnemonic_stop_hook.sh checks for pending summary
    ↓ (found)
mnemonic_postflight.sh:
  - mnemonic_should_upsert.sh → new vs update vs confirm vs skip
  - ingest-session → archive chunks + review items
  - run-job daily_consolidation → ACTIVE.md refresh, staleness, duplicates
    ↓
/tmp/mnemo_pending_summary.md deleted
```

> **Note:** The `PreCompact` hook fires for all agents, but only the `claude` agent writes `/tmp/mnemo_pending_summary.md`. If no pending summary exists, the hook now skips writing a raw session file.

---

## Manual Use

### Retrieve context for a query

```bash
cd /Users/jianyulong/ai_memory
scripts/mnemonic_context.sh "how do we handle auth in this repo" --query-type-hint how_to
```

### Check if retrieval is warranted

```bash
scripts/mnemonic_should_retrieve.sh --task "debug the token refresh bug"
# exit 0 = retrieve; exit 1 = skip
```

### Check if a summary is worth ingesting

```bash
scripts/mnemonic_should_writeback.sh --mode failure --summary-file /tmp/my_summary.md
```

### Manually ingest a summary

```bash
scripts/mnemonic_postflight.sh \
  --agent claude \
  --slug claude-2026-04-10-session \
  --summary-file /tmp/my_summary.md \
  --tag debugging --tag auth
```

### Force retrieval after a failure

```bash
scripts/mnemonic_should_retrieve.sh --task "fix token refresh" --after-failure
# always exit 0
```

---

## Override / Disable

**Temporarily skip preflight retrieval for a session**: the hook checks `mnemonic_should_retrieve.sh` — start your prompt with a trivial command pattern or set `MNEMO_SKIP=1` if you add that flag to the scripts.

**Skip writeback for a session**: simply don't write `/tmp/mnemo_pending_summary.md`. The Stop hook no-ops if the file doesn't exist.

**Force writeback manually** (if Stop hook isn't available):
```bash
scripts/mnemonic_postflight.sh --agent claude --slug claude-$(date +%Y-%m-%d-%H%M%S) \
  --summary-file /tmp/mnemo_pending_summary.md
```

---

## Inspect What Was Written

After a session with writeback:
- New archive chunks: `brain/data/archive.db` (table `archive_chunks`)
- New review items: `brain/wiki/review/`
- Raw session file: `brain/raw/sessions/`
- ACTIVE.md refreshed: `brain/system/ACTIVE.md`

Query the archive to verify:
```bash
cd /Users/jianyulong/ai_memory
mnemonicos retrieve "what I learned about auth" --top-k 5
```

---

## Shared Scripts (also used by Codex)

| Script | Shared? | Purpose |
|---|---|---|
| `mnemonic_context.sh` | Yes | Hybrid retrieval → compact context block |
| `mnemonic_postflight.sh` | Yes | Ingest summary + daily consolidation |
| `mnemonic_should_retrieve.sh` | Yes | Retrieval decision heuristic |
| `mnemonic_should_writeback.sh` | Yes | Writeback decision heuristic |
| `mnemonic_should_upsert.sh` | Yes | Dedupe/update decision before ingest |
| `mnemonic_hook_preflight.sh` | Claude Code only | `UserPromptSubmit` hook entry point |
| `mnemonic_stop_hook.sh` | Claude Code only | `Stop` hook entry point |
| `mnemonic_precompact_dump.sh` | Claude Code only | `PreCompact` hook — raw dump before compaction |

---
name: mnemonicos-bridge
description: "Use at the start of ANY non-trivial task in or with MnemonicOS — coding, debugging, architecture, research, knowledge questions. Enforces: read injected Memory Context, interpret retrieval state, run manual retrieval if hook missed, re-query after first meaningful failure, write structured summary on exit."
---

# MnemonicOS Bridge

Memory-aware behavior for every task. The UserPromptSubmit hook may have already injected a `## Memory Context` block — this skill tells you what to do with it and when to go further.

## Step 1 — Read Hook-Injected Context

If a `## Memory Context` block is present at the top of the prompt, read it now. It contains:
- `retrieval_state` — how confident MnemonicOS is
- `recommendation` — `reuse`, `reuse_with_verification`, or none
- `Primary Memory` / `Related by Topic` / `Archive Evidence` sections

Interpret retrieval state:

| State | Action |
|---|---|
| `sufficient_current` + `reuse` | Use MnemonicOS as primary source. Do NOT re-query. |
| `sufficient_historical` + `reuse` | Use only for historical/background questions. |
| `partial` + `reuse_with_verification` | Use as scaffold, then verify with fresh tools/code. |
| `insufficient_no_hit` | Skip MnemonicOS. Do fresh research. |
| `insufficient_stale` | MnemonicOS hit is old. Verify before trusting. |
| `insufficient_contested` | Contradiction open. Check `brain/wiki/review/`. Do not trust. |

If no `## Memory Context` block is present (hook missed or fired too early), go to Step 2.

## Step 2 — Manual Retrieval (when hook missed or state is insufficient)

Check if retrieval is warranted:

```bash
cd /Users/jianyulong/ai_memory && scripts/mnemonic_should_retrieve.sh --task "<user request>"
```

If exit 0, run retrieval:

```bash
cd /Users/jianyulong/ai_memory && scripts/mnemonic_context.sh "<derived query>" --json
```

Interpret the result using the same state table above.

## Step 3 — During Work: Re-retrieval After First Failure

After **any** meaningful failure, re-query before retrying. Mandatory, not optional.

Meaningful failure = error needing diagnosis, wrong assumption discovered, stale procedure, changed approach, rollback/rework.

```bash
cd /Users/jianyulong/ai_memory && scripts/mnemonic_context.sh "<failure-focused query>" --json
```

## Step 4 — Writeback

Write back when:
- Meaningful failure occurred (always)
- Reusable success (changed practice, better command, confirmed/fixed stale procedure)
- Knowledge session (explained concept, compared approaches)

Do NOT write back: trivial sessions (`ls`, `pwd`, routine reads), pure restatements of existing notes.

Draft the summary in a scratch file first, then run the deduplication check on it:

```bash
cd /Users/jianyulong/ai_memory && scripts/mnemonic_should_upsert.sh --summary-file <draft.md> --json
```

Priority: `confirm` > `update` > `new`. Only create new notes when no existing note covers the subject.

Queue the summary for the Stop hook with the helper, which always creates a **new** file under `/tmp/mnemo_pending/` (never overwrites another session's or another agent's pending summary):

```bash
cd /Users/jianyulong/ai_memory && scripts/mnemonic_pending_summary.sh --agent claude --file <draft.md>
```

Do NOT write `/tmp/mnemo_pending_summary.md` directly any more; that single path is shared with Codex and gets overwritten. (The Stop hook still accepts it for backward compatibility when its `agent:` header is `claude`.) The Stop hook ingests only files whose `agent:` header is `claude` and deletes each one only after it was accepted.

Required header fields:

```
agent: claude
session_date: YYYY-MM-DD
task_kind: coding|debugging|knowledge|architecture|research
mnemos_retrieval_state: <state from retrieval>
existing_note_candidates: <note ids or none>
writeback_intent: new|update|confirm|incident_only|procedure_refinement|skip
update_target_ids: <note ids or none>
fresh_research_needed: yes|no
fresh_sources_checked: <list or none>
```

For **knowledge sessions**, include a fenced `memory` block with a concept note. The `summary` field must be a gene string: `"WHEN [trigger] → DO [action] (NOT [anti-pattern])"`.

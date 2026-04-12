# MnemonicOS × Codex Integration

## Goal

Codex should follow the same reuse-first memory protocol as Claude:

1. Query MnemonicOS first for relevant coding, project, or knowledge tasks.
2. Reuse current memory when sufficient.
3. Treat stale/contested memory as hints, then verify or research fresh.
4. After the first meaningful failure, retrieve again before more retries.
5. Write back meaningful failures, reusable successes, and durable knowledge sessions.
6. Avoid duplicate note creation by deciding `new` vs `update` vs `confirm` vs `skip` before ingest.

## Shared Scripts

Use the same repo-local scripts as Claude:

- `scripts/mnemonic_context.sh`
- `scripts/mnemonic_should_retrieve.sh`
- `scripts/mnemonic_should_writeback.sh`
- `scripts/mnemonic_should_upsert.sh`
- `scripts/mnemonic_postflight.sh`

## Recommended Codex Workflow

### Preflight

```bash
scripts/mnemonic_should_retrieve.sh --task "debug the auth deploy failure"
scripts/mnemonic_context.sh "debug the auth deploy failure" --json
```

Use the `retrieval_state` / `recommendation` pair:

- `sufficient_current` + `reuse`: answer or work from MnemonicOS first
- `sufficient_historical` + `reuse`: use it only for historical questions
- `partial` + `reuse_with_verification`: use memory as a scaffold, then verify
- `insufficient_*` + `research_fresh`: do normal repo/tool/web research

### Post-task writeback

Prepare a structured summary, then:

```bash
scripts/mnemonic_should_writeback.sh --mode knowledge --summary-file /tmp/mnemo_pending_summary.md
scripts/mnemonic_should_upsert.sh --summary-file /tmp/mnemo_pending_summary.md --json
scripts/mnemonic_postflight.sh --agent codex --slug codex-$(date +%Y-%m-%d-%H%M%S) --summary-file /tmp/mnemo_pending_summary.md
```

## Summary Fields

Codex summaries should include these header fields before the markdown sections:

```text
agent: codex
session_date: YYYY-MM-DD
task_kind: coding|debugging|knowledge|architecture|research
mnemos_retrieval_state: sufficient_current|sufficient_historical|insufficient_no_hit|insufficient_stale|insufficient_contested|partial
existing_note_candidates: note/id, note/id
writeback_intent: new|update|confirm|incident_only|procedure_refinement|skip
update_target_ids: note/id, note/id
fresh_research_needed: yes|no
fresh_sources_checked: source, source
```

These fields let the postflight path avoid duplicate note creation.

## Installing the Codex Skill

The repo ships a reusable Codex skill source under `codex_skills/mnemonicos-bridge/`.

Install or refresh it into your local Codex environment with:

```bash
scripts/install_codex_skill.sh
```

The install script copies the repo skill into `$CODEX_HOME/skills/mnemonicos-bridge` or `~/.codex/skills/mnemonicos-bridge`.

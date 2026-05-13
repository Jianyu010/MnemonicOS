# Building a Codex Bridge for MnemonicOS

## Boundary: MnemonicOS API vs. Bridge Implementation

MnemonicOS owns the capture API and storage contract. A bridge implementation only
adapts Codex runtime events into that contract.

MnemonicOS capture API boundary:

- `capture begin-episode` creates a durable raw episode.
- `capture append-event` appends one structured event to that episode.
- `capture end-episode` finalizes the episode, hashes it, indexes it in
  `raw_episodes`, and enqueues extraction.
- `events.jsonl` is the faithful source of truth.
- `transcript.md` is only a readable text projection of user and assistant
  message events.
- Large payloads may be externalized by MnemonicOS into episode artifacts.

Codex bridge implementation boundary:

- Discover Codex turn boundaries.
- Read Codex payloads, transcript entries, hook events, or notify events.
- Normalize those records into MnemonicOS event types.
- Never decide what should be promoted into memory.
- Never mutate MnemonicOS wiki notes directly.
- Never rely on `transcript.md` as a complete record.

The bridge is therefore a lossless adapter, not a memory-policy layer.

## Target Architecture

A capable Codex bridge has three parts:

1. Turn start: begin a MnemonicOS episode and append the user prompt.
2. Turn event import: append every structured runtime event as it happens or at
   turn completion.
3. Turn close: append the final assistant message, append a conclusion event when
   available, and end the episode.

The canonical raw layout produced by MnemonicOS is:

```text
brain/raw/sessions/<conversation_id>/
  manifest.json
  episodes/<episode_id>/
    manifest.json
    events.jsonl
    transcript.md
    artifacts/
```

## Event Contract

Use these event types for a faithful Codex bridge:

```text
episode_started
user_message
assistant_message
tool_call
tool_result
file_changed
file_diff
artifact_created
retrieval_packet
skill_use
web_fetch
conclusion
plan_update
agent_delegation
failure
branch_started
branch_completed
conversation_reference
episode_ended
```

Recommended payload fields:

```json
{
  "source": "codex_transcript",
  "turn_id": "turn-123",
  "tool_name": "exec_command",
  "call_id": "call-1",
  "input": {"cmd": "python3 -m unittest"},
  "output": "OK",
  "is_error": false,
  "raw_payload": {}
}
```

Keep `raw_payload` whenever possible. Normalized fields make later queries easy;
the raw payload preserves details the normalizer does not yet understand.

## Step 1: Begin an Episode

At Codex turn start, call:

```bash
mnemonicos --config config/memory.toml capture begin-episode \
  --bridge-id codex \
  --conversation-id "$THREAD_ID" \
  --episode-id "$TURN_ID" \
  --cwd "$PWD" \
  --model "$MODEL" \
  --agent-runtime codex \
  --platform-thread-id "$THREAD_ID" \
  --metadata-json '{"hook_event":"UserPromptSubmit"}' \
  --json
```

Store the returned `conversation_id` and `episode_id` in a per-thread state file.
All later events for the same turn must use those IDs.

Append the submitted user prompt:

```bash
mnemonicos --config config/memory.toml capture append-event \
  --bridge-id codex \
  --conversation-id "$CONVERSATION_ID" \
  --episode-id "$EPISODE_ID" \
  --type user_message \
  --payload-json '{"content":"...","source":"codex_user_prompt_submit"}' \
  --json
```

## Step 2: Capture Retrieval

If the bridge injects MnemonicOS context, append a `retrieval_packet` event even
when the retrieval result is empty:

```json
{
  "source": "codex_user_prompt_submit",
  "turn_id": "turn-123",
  "query": "task text or derived query",
  "state": "partial",
  "recommendation": "reuse_with_verification",
  "hit_ids": ["concept/example"],
  "stale_ids": [],
  "contested_ids": [],
  "context_markdown": "<!-- MnemonicOS context block -->..."
}
```

This is the provenance needed for passive feedback and retrieval quality audits.

## Step 3: Capture Skill Use

When Codex uses a skill, append `skill_use`:

```json
{
  "source": "codex_runtime",
  "skill": "mnemonicos-bridge",
  "path": "/Users/example/.codex/skills/mnemonicos-bridge/SKILL.md",
  "reason": "memory-aware coding task",
  "turn_id": "turn-123"
}
```

This can be emitted by an explicit bridge hook, by a wrapper around skill loading,
or by the turn-completion importer if Codex exposes skill-use events.

## Step 4: Capture Tool Calls and Results

Map Codex response items like this:

```text
function_call / custom_tool_call          -> tool_call
function_call_output / custom_tool_output -> tool_result
```

For calls, preserve `tool_name`, `call_id`, parsed JSON arguments as `input`, and
the raw Codex payload.

For results, preserve `tool_name` if known, `call_id`, `is_error`, text output,
and the raw payload. MnemonicOS will externalize large payloads automatically.

## Step 5: Capture Web Fetches

Map web/search/open events into `web_fetch`:

```json
{
  "source": "codex_event_msg",
  "event_type": "web_search_end",
  "url": "https://example.com/doc",
  "title": "Example Doc",
  "status": 200,
  "raw_payload": {}
}
```

If the fetched body is large, store a summary inline and attach the full body as
an artifact or let MnemonicOS externalize the payload.

## Step 6: Capture File Changes

Use `file_diff` for patch-style changes:

```json
{
  "source": "codex_event_msg",
  "path": "src/example.py",
  "before_sha256": "optional",
  "after_sha256": "optional",
  "diff": "@@ ...",
  "raw_payload": {}
}
```

Use `file_changed` for final file snapshots when a diff is unavailable:

```json
{
  "source": "codex_file_hook",
  "path": "src/example.py",
  "operation": "edit",
  "size_bytes": 1234,
  "after_sha256": "..."
}
```

Use `artifact_created` for generated reports, images, notebooks, exported files,
or other non-source artifacts.

## Step 7: Capture Conclusions

At turn end, append a `conclusion` event:

```json
{
  "source": "codex_stop_hook",
  "summary": "Implemented richer Codex capture and verified tests.",
  "commands_run": ["python3 -m unittest tests.test_hook_adapter"],
  "files_modified": ["scripts/mnemonic_codex_notify_bridge.py"],
  "artifacts_created": [],
  "retrieval_used": true,
  "open_risks": []
}
```

This is a summary of the raw events, not a replacement for them.

## Step 8: End the Episode

At turn completion:

```bash
mnemonicos --config config/memory.toml capture end-episode \
  --bridge-id codex \
  --conversation-id "$CONVERSATION_ID" \
  --episode-id "$EPISODE_ID" \
  --status success \
  --metadata-json '{"hook_event":"Stop","turn_id":"turn-123"}' \
  --json
```

This writes `episode_ended`, updates manifests, computes the content hash, and
indexes the episode in `raw_episodes`.

## Turn-Completion Importer Pattern

If Codex can provide a structured `agent-turn-complete` notify payload, pass
embedded events to the importer:

```json
{
  "type": "agent-turn-complete",
  "thread-id": "thread-123",
  "turn-id": "turn-123",
  "input-messages": ["user prompt"],
  "last-assistant-message": "final answer",
  "events": [
    {"type": "retrieval_packet", "payload": {"state": "partial"}},
    {"type": "skill_use", "payload": {"skill": "mnemonicos-bridge"}},
    {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}},
    {"type": "response_item", "payload": {"type": "function_call_output", "output": "OK"}},
    {"type": "event_msg", "payload": {"type": "web_search_end", "url": "https://example.com"}},
    {"type": "event_msg", "payload": {"type": "patch_apply_end", "changes": []}},
    {"type": "conclusion", "payload": {"summary": "done"}}
  ]
}
```

The importer should:

- use `thread-id` as the conversation ID;
- use `turn-id` as the episode ID;
- append input messages and final assistant message;
- normalize embedded `response_item` and `event_msg` records;
- append direct structured events unchanged except for adding source metadata;
- end the episode once all events are written.

## Verification Checklist

A bridge is capable when an E2E test can assert that a single Codex turn records:

- user prompt;
- final assistant message;
- retrieval packet;
- skill use;
- at least one tool call;
- at least one tool result;
- web fetch/search/open event;
- file diff or file change;
- generated artifact;
- final conclusion;
- episode hash and raw episode DB index row.

The readable transcript should contain only human conversation. The complete
record should be reconstructed from `events.jsonl` plus any externalized payloads
under `artifacts/`.

## Operational Notes

- Hooks should never block Codex completion. On capture failure, log and continue.
- Set `MNEMOS_AUTO_TRIGGER_PIPELINE=0` inside hooks; let the daemon process
  extraction/promotion outside the interactive path.
- Use one state file per Codex thread or session so parallel turns do not collide.
- Keep the bridge id as `codex` for all events emitted by Codex.
- Prefer append-only event capture. Do not edit previous event rows.
- When Codex changes payload shapes, add normalizers that preserve `raw_payload`
  before adding refined fields.


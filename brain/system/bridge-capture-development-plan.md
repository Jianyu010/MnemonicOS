# Bridge Capture Development Plan

## Summary

MnemonicOS bridges should stop relying on LLM-discretionary skill calls for raw
session capture. Retrieval and writeback can remain agent-invoked, but raw
capture must be software-enforced by bridge middleware, platform hooks, or local
wrappers.

The bridge should observe completed turns and tool activity, then append raw
events to MnemonicOS. MnemonicOS owns the file layout, append semantics,
manifests, attachment storage, and later extraction pipeline.

---

## Core Model

The archival primitive is not a "complete session". Agentic conversations can be
reopened days later, continued from old threads, branched into subagents, or
started fresh while referring to old work.

Use three identities:

- `conversation_id`: durable thread identity across reopened chats.
- `episode_id`: one active visit/run/span within a conversation.
- `task_id`: user-visible work unit; optional and may span many events.

Suggested raw layout:

```text
brain/raw/sessions/{conversation_id}/
    manifest.json
    episodes/
        {episode_id}/
            events.jsonl
            transcript.md
            artifacts/
```

`events.jsonl` is the spine. Attachments store large outputs, diffs, generated
files, command logs, search snapshots, or child branch logs.

---

## Reliability Principle

Raw capture must not depend on the LLM deciding to call a skill or tool.

Preferred enforcement layers, strongest last:

1. Voluntary LLM/tool call: acceptable for retrieval and explicit writeback, not
   acceptable for guaranteed raw capture.
2. Agent wrapper or bridge middleware: intercepts turns/tool activity and calls
   MnemonicOS automatically.
3. Platform-native lifecycle hooks: best case; hook adapter forwards observed
   events to MnemonicOS.

Minimum reliable capture:

- user message after submission
- assistant final output after completion
- tool call and tool result
- observable file edits/diffs as attachments
- errors, retries, and failures
- episode start/end metadata

Token-level streaming capture is not required for v1. Per-turn and per-tool
append is enough.

---

## Bridge API

Add bridge-facing capture operations.

### `begin_episode`

Purpose:

- Create or open a durable conversation directory.
- Create a new episode directory.
- Initialize `manifest.json`, `events.jsonl`, `transcript.md`, and `artifacts/`.

Inputs:

- `bridge_id`
- optional `conversation_id`
- optional `episode_id`
- optional `parent_conversation_id`
- optional `related_conversation_ids`
- metadata such as cwd, repo, branch, model, agent runtime, UI thread id

Output:

- `conversation_id`
- `episode_id`
- paths for event log, transcript, and artifact directory

### `append_event`

Purpose:

- Append one complete event record to `events.jsonl`.
- Store oversized payloads in `artifacts/` and replace inline content with a
  pointer.
- Update `transcript.md` for user/assistant message events.

Event envelope:

```json
{
  "seq": 1,
  "ts": "2026-04-27T16:30:00+08:00",
  "conversation_id": "codex-thread-abc",
  "episode_id": "2026-04-27T163000+0800",
  "bridge_id": "codex",
  "task_id": "optional-task-id",
  "branch_id": "main",
  "type": "user_message",
  "payload": {}
}
```

Common event types:

- `episode_started`
- `user_message`
- `assistant_message`
- `tool_call`
- `tool_result`
- `file_changed`
- `artifact_created`
- `retrieval_packet`
- `failure`
- `branch_started`
- `branch_completed`
- `episode_ended`

Large payload rule:

- Keep the JSONL line compact and replayable.
- Store large command output, search results, diffs, generated files, screenshots,
  and branch logs under `artifacts/`.
- Put attachment pointers in the event payload.

### `end_episode`

Purpose:

- Append `episode_ended`.
- Flush final metadata.
- Record event count, content hashes, status, and end timestamp.

Inputs:

- `conversation_id`
- `episode_id`
- status: `success`, `failure`, `partial`, `abandoned`
- optional final metadata

---

## Branching And Multi-Agent Work

Use `branch_id` on events for lightweight branching.

For substantial subagent work, write a child event log and reference it:

```json
{
  "type": "branch_started",
  "branch_id": "agent-a",
  "parent_seq": 21,
  "artifact": "artifacts/agent-a/events.jsonl"
}
```

This keeps the parent episode readable while preserving child traces in full.

---

## Conversation Continuation

Reopening an old chat should create a new episode under the same
`conversation_id`, not mutate the old episode.

Fresh chats that refer to old chats should stay separate conversations with soft
links:

```json
{
  "type": "conversation_reference",
  "target_conversation_id": "old-chat-id",
  "confidence": "user_mentioned"
}
```

MnemonicOS can later strengthen or reject these links during extraction and
promotion.

---

## Implementation Shape

Add `src/second_brain/session_capture.py`:

- normalize ids and paths
- create conversation/episode directories
- append JSONL with monotonic `seq`
- flush after append
- write attachments for large payloads
- update human-readable transcript
- update manifest counters and hashes

Add models:

- `BeginEpisodeRequest`
- `BeginEpisodeResult`
- `AppendEventRequest`
- `AppendEventResult`
- `EndEpisodeRequest`
- `EndEpisodeResult`

Add CLI:

```bash
mnemonicos capture begin-episode ...
mnemonicos capture append-event ...
mnemonicos capture end-episode ...
```

Add HTTP:

```text
POST /v1/capture/begin-episode
POST /v1/capture/append-event
POST /v1/capture/end-episode
```

Keep the current `ingest-session` summary flow temporarily as legacy
compatibility. New bridges should prefer comprehensive capture APIs.

---

## Test Plan

- Begin episode creates the expected directory tree.
- Begin episode initializes conversation and episode manifests.
- Append user/assistant messages writes JSONL and updates `transcript.md`.
- Append tool call/result writes JSONL.
- Large payloads become artifact files with event pointers.
- Sequence numbers are monotonic across appends.
- Reopening a conversation creates a new episode without changing old episode
  logs.
- Branch events can carry `branch_id` and/or point to child logs.
- End episode records status, end time, event count, and hashes.
- Legacy `ingest-session` continues to work unchanged.

---

## Assumptions

- Agent runtimes can expose events through hooks, wrappers, middleware, or local
  adapters.
- MnemonicOS should own raw capture layout and append semantics.
- Bridge adapters should forward observed events, not interpret them.
- Extraction into structured wiki candidates happens later over captured raw
  episodes.

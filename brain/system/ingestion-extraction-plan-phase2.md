# LLM/VLM Layer Phase 2: Extraction Queue and Candidate Store

## Goal

Create the sole west-to-candidate queue and persistence layer.

## Scope

Add SQLite-backed extraction state:

- `extraction_queue`
- `extraction_visual_snippets`
- `extraction_candidates`

The queue is source-agnostic. It identifies work by west-flange source identity,
not by legacy session summaries.

## Data Model

`extraction_queue` records:

- source kind: `artifact`, `episode`, or `session`
- artifact source: `archive_id`
- episode/session source: `conversation_id` and `episode_id`
- bridge/session/task identity when provided
- duplicate state
- status: `pending`, `processing`, `processed`, `skipped`, `error`
- attempt count
- timestamps
- last error

`extraction_visual_snippets` records:

- queue id
- artifact id
- marker
- source locator
- snippet JSON
- model metadata

`extraction_candidates` records:

- queue id
- candidate type
- candidate payload JSON
- validation status
- validation errors
- model metadata
- promotion-readiness state

## Milestones

1. Migration creates the extraction tables and indexes.
2. Queue API can enqueue artifact sources.
3. Queue API can enqueue episode/session sources.
4. Queue status can be inspected.
5. Queue rows can transition through pending, processing, processed, skipped,
   and error.
6. No queue operation writes `brain/wiki/`.

## Verification

- Migration test confirms table creation and constraints.
- Artifact queueing requires `archive_id`.
- Episode queueing requires `conversation_id` and `episode_id`.
- Invalid source kind is rejected.
- Duplicate queue attempts are deterministic according to the chosen queue
  identity rule.
- Status inspection returns candidate counts and errors.
- A test asserts no wiki files are created by queue operations.

## Out Of Scope

- Opening projection leases.
- Calling model providers.
- Candidate validation.
- Deterministic wiki promotion.


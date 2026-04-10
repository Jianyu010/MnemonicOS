# Implementation Plan

Current status: Phase 4 is implemented locally in this repo. The system now has
trust/usefulness scoring, freshness states, contradiction reviews, relearn
tasks, and operator-facing maintenance summaries on top of the existing graph,
retrieval, and ingest layers. The remaining work is refinement rather than
major missing architecture.

## Goal

Turn the spec into a small local service that can:

1. ingest raw sessions and documents into the vault
2. synchronize markdown notes into SQLite search indexes
3. run consolidation and hygiene jobs
4. answer retrieval requests through a minimal API

The intended runtime is a single local process with SQLite, optional local
embeddings, and no required cloud dependencies.

## Repository Layout

```text
brain/
  system/
  raw/
  wiki/
  templates/
config/
api/
docs/
migrations/
```

## Component Boundaries

### 1. Vault Scanner

Responsibility:

- walk `brain/system`, `brain/wiki`, and `brain/raw`
- detect added, changed, and deleted files
- compute `content_hash`
- update `sync_state`

Rules:

- raw files are append-only; if content changes, mark as integrity issue
- wiki and system files are mutable; rebuild derived caches on change
- treat direct human edits as first-class, not exceptional

### 2. Note Parser

Responsibility:

- parse markdown frontmatter into structured note records
- validate required fields by type
- extract body text for `notes_search`
- normalize aliases, tags, and linked entities

Validation policy:

- invalid note does not block the whole sync
- invalid note gets `sync_state.parse_status = 'error'`
- error summary is surfaced in a review item or job log

### 3. Ingest Pipeline

Responsibility:

- write raw session/doc files first
- chunk and summarize archive content
- extract candidate memories
- apply promotion rules
- upsert canonical notes or review items

Atomicity:

- one ingest transaction per source
- if note upserts fail after raw save, keep the raw source and record a failed job

### 4. Search Materializer

Responsibility:

- refresh `notes_search` rows after note or alias changes
- keep `notes_fts` and `chunks_fts` synchronized through triggers
- re-embed only summaries that changed
- rebuild graph edges for explicit note relationships such as owners, repo
  links, and linked decisions/procedures

Important rule:

- never query note markdown files directly in the retrieval path
- retrieval reads SQLite caches only
- vault files are parsed by sync jobs, not by live queries

### 5. Retrieval Service

Responsibility:

- exact lookup
- BM25 retrieval
- optional semantic retrieval
- graph expansion from top focal notes
- reranking with trust and freshness signals
- context assembly
- retrieval logging

Degradation:

- if embeddings are unavailable, return exact + BM25
- if the graph cache is unavailable, return exact + BM25 + semantic
- if rerank training data is sparse, fall back to heuristic weights
- if classifier confidence is low, widen rather than narrow
- if no canonical notes score well, fall back to archive chunks

### 6. Consolidation Worker

Responsibility:

- duplicate detection
- alias propagation proposals
- stale fact detection
- contradiction review generation
- procedure audit

Output:

- updated notes where confidence is high
- review items under `brain/wiki/review/` where confidence is medium
- job log entries in SQLite and `brain/wiki/log.md`

## Suggested Module Layout

```text
src/second_brain/
  config.py
  paths.py
  db.py
  models.py
  parser.py
  summaries.py
  semantics.py
  graph.py
  trust.py
  review.py
  sync.py
  ingest.py
  retrieval.py
  jobs.py
  ops.py
```

Minimal responsibilities:

- `config.py`: read `config/memory.example.toml`-style settings
- `paths.py`: resolve vault-relative paths safely
- `db.py`: open SQLite, run migrations, transaction helpers
- `models.py`: request and response models
- `parser.py`: frontmatter parsing and note validation
- `summaries.py`: extractive summaries for notes and archive chunks
- `semantics.py`: local hash-vector semantic channel
- `graph.py`: SQLite-backed note graph materialization and expansion
- `trust.py`: note trust/usefulness, freshness scoring, and rerank weight training
- `review.py`: inspectable review item writer
- `sync.py`: scan changed files and refresh DB caches
- `ingest.py`: raw write, chunking, promotion, upsert
- `retrieval.py`: exact, BM25, semantic, rerank, log
- `jobs.py`: daily and weekly maintenance jobs
- `ops.py`: `ACTIVE.md` refresh and retrieval-miss eval candidate mining

## Sync Jobs

### Job: `sync_vault_incremental`

Trigger:

- service startup
- after every ingest
- after every `git pull`
- on explicit `POST /v1/sync/vault`

Inputs:

- changed paths from filesystem scan

Steps:

1. detect changed or deleted files under `brain/system` and `brain/wiki`
2. parse valid notes
3. upsert `notes`
4. replace aliases for each touched note
5. rebuild that note's `notes_search` row
6. rebuild graph edges for explicit note relationships
7. let FTS triggers update `notes_fts`
8. mark deleted notes as removed from SQLite caches
9. update `sync_state`

Failure behavior:

- continue syncing remaining files
- record parse failures in `job_runs`
- create review items only for semantically ambiguous issues, not syntax errors

### Job: `ingest_session`

Trigger:

- `POST /v1/ingest/session`

Inputs:

- agent name
- session slug
- markdown content
- optional tags

Steps:

1. allocate archive ID
2. write raw session markdown
3. insert `archive`
4. chunk and summarize content
5. insert `archive_chunks`
6. extract candidates
7. apply promotion filter
8. upsert notes or review items
9. append to `brain/wiki/log.md`
10. run `sync_vault_incremental`

### Job: `daily_consolidation`

Trigger:

- once per day

Steps:

1. duplicate scan from aliases and lexical similarity
2. summary refresh for recently changed notes
3. alias suggestion mining from recent archive chunks
4. wikilink repair proposals
5. stale note detection
6. recompute note trust and freshness
7. create reverify/crosscheck relearn tasks
8. incremental vector refresh
9. mine retrieval misses into `evals/candidates.jsonl`
10. refresh `brain/system/ACTIVE.md` with review, contradiction, stale, and relearn surfaces
11. append one daily summary line to `brain/wiki/log.md`

### Job: `weekly_hygiene`

Trigger:

- once per week

Steps:

1. drain high-confidence review items
2. contradiction checks
3. procedure audit
4. train/update rerank weights
5. run retrieval evals
6. refresh synthesis pages in `brain/wiki/overviews`

## SQLite Write Discipline

Always write these in one transaction for a touched note:

1. `notes`
2. `aliases` delete-and-replace for that `note_id`
3. `claims` update if the note type uses claims
4. `notes_search` full replace for that `note_id`

`notes_fts` is derived from `notes_search` through triggers.

## Canonical Upsert Rules

### Mutable in place

- `person`
- `project`
- `repo`
- `concept`
- `incident`
- `source`

### Never overwrite historical truth

- `decision`
  - changing the answer creates a new note
  - old note gets `status = superseded`, `valid_to`, and `superseded_by`

- `procedure`
  - new evidence updates draft
  - promotion changes location and status
  - invalid procedures move to `retired/`

### Immutable

- raw sessions
- imported docs
- archive chunks

## Retrieval Flow

### Channel order

1. pinned memory
2. exact alias or ID hits
3. BM25 over canonical notes
4. semantic retrieval over summaries
5. graph expansion from the strongest focal notes
6. rerank merged candidates
7. archive fallback

### Live query budget

- exact lookup: under 5 ms
- BM25: under 20 ms for small local corpora
- semantic search: under 30 ms local
- total p95 target: under 150 ms without LLM generation

### Assembly policy

- inject `MEMORY.md`, `USER.md`, `ACTIVE.md`
- include top 2-4 notes
- include archive support only if needed
- keep note summaries short so the retrieval layer returns concise context blocks

## Recommended Build Order

### Milestone 1

- migrations
- folder scaffold
- note parser
- sync job
- exact + BM25 retrieval

Definition of done:

- direct edits in the vault become searchable after sync
- exact alias lookup works
- BM25 returns the right note on a seed eval set

### Milestone 2

- ingest endpoint
- chunking and archive search
- review item creation
- daily consolidation

Definition of done:

- a session can be ingested end to end
- review items are created for medium-confidence merges
- stale notes surface in `ACTIVE.md`

Status:

- ingest pipeline implemented
- archive chunking implemented
- review item creation implemented
- daily consolidation implemented as a maintenance job
- `ACTIVE.md` surfacing is still a follow-up refinement

### Milestone 3

- embedding index
- semantic retrieval
- retrieval logs and eval runner
- weekly hygiene
- graph cache for explicit note relationships

Definition of done:

- top-1 and top-5 metrics are tracked weekly
- semantic search helps paraphrase recall without breaking exact recall
- graph expansion surfaces related notes that lexical and semantic channels miss

Status:

- embedding index implemented
- semantic retrieval implemented
- retrieval logs and eval runner implemented
- weekly hygiene implemented
- graph cache implemented for explicit note links such as `owners`, `repo`,
  and `linked_*`

### Milestone 4

- trust/usefulness scoring
- freshness state computation
- contradiction review generation
- relearn task queue
- maintenance overview generation

Definition of done:

- stale current-truth notes are demoted without losing historical recall
- contradiction backlog and relearn queue are visible in generated operator files
- rerank weights train locally when labeled samples are sufficient
- the system remains responsive and does not autonomously rewrite truth

## Minimal Runtime API

See `api/openapi.yaml`.

The smallest useful API has four actions:

1. `GET /v1/health`
2. `POST /v1/ingest/session`
3. `POST /v1/sync/vault`
4. `POST /v1/retrieve`
5. `POST /v1/jobs/run`

This is enough to operate the local memory service without overdesigning orchestration.

## First Real Build Tasks

1. Implement frontmatter parsing and per-type validation.
2. Implement SQLite migration runner and connection bootstrap.
3. Implement `sync_vault_incremental` with full replace of `notes_search`.
4. Implement exact alias lookup plus BM25 retrieval.
5. Seed `evals/queries.jsonl` with 20-30 real queries.
6. Add embeddings only after BM25 behavior looks clean.

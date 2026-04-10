# MnemonicOS

MnemonicOS is a local-first memory system for Codex and Claude. The markdown
vault is the source of truth; SQLite search tables, semantic vectors, the note
graph, and trust/freshness state are rebuildable caches.

## Design

```mermaid
flowchart TD
    A["Humans / Agents"] --> B["Vault<br/>brain/"]
    B --> C["SQLite cache<br/>notes + FTS5 + archive"]
    B --> D["Semantic cache<br/>local hash vectors"]
    B --> H["Graph cache<br/>explicit note links"]
    B --> T["Trust/Freshness cache<br/>staleness + usefulness"]
    C --> E["Retriever"]
    D --> E
    H --> E
    T --> E
    B --> F["Pinned memory<br/>MEMORY.md USER.md ACTIVE.md"]
    F --> E
    E --> G["LLM context"]
```

Why it is structured this way:

- Inspectable: every durable memory lives as markdown in `brain/`.
- Portable: git can move the whole system without a hosted backend.
- Recoverable: `archive.db`, vectors, graph indexes, and freshness/trust state can be rebuilt.
- Conservative: promotion rules matter more than fancy ranking.

## Memory Layout

```mermaid
flowchart LR
    L1["Pinned core<br/>system/*.md"] --> L2["Canonical wiki<br/>wiki/*"]
    L2 --> L3["Procedures<br/>drafts active retired"]
    L3 --> L4["Archive<br/>raw sessions + chunk store"]
    L4 --> L5["Derived caches<br/>FTS5 + vectors + graph"]
```

```mermaid
flowchart TD
    brain["brain/"]
    brain --> system["system/<br/>MEMORY.md<br/>USER.md<br/>ACTIVE.md<br/>AGENTS.md"]
    brain --> raw["raw/<br/>sessions docs web_clips assets"]
    brain --> wiki["wiki/<br/>people projects repos decisions concepts incidents procedures journals overviews sources review"]
    brain --> data["data/<br/>archive.db"]
    brain --> locks["locks/"]
    brain --> templates["templates/"]
```

## What Ships Today

Phase 4 is implemented.

```mermaid
flowchart LR
    A["sync"] --> B["Parse notes"]
    B --> C["FTS5 + metadata index"]
    B --> D["Semantic note vectors"]
    B --> J["Graph edges from explicit links"]
    B --> K["Trust + freshness signals"]
    E["ingest-session"] --> F["raw session file"]
    E --> G["archive chunks + summaries"]
    E --> H["promoted notes or review items"]
    C --> I["hybrid retrieval"]
    D --> I
    J --> I
    K --> I
    G --> I
    H --> I
```

- incremental vault sync into SQLite
- note summaries generated during sync
- local semantic retrieval channel using deterministic hashed vectors
- graph retrieval layer from explicit note relationships like `owners`, `repo`, and `linked_*`
- trust/usefulness scoring from retrieval history
- freshness states that demote stale current-truth notes without deleting history
- raw session ingest with archive chunking and chunk summaries
- explicit memory promotion into canonical notes
- review item generation for medium-confidence promotions and contradictions
- maintenance jobs for duplicate aliases, freshness, contradiction, and relearn queues
- `ACTIVE.md` refresh from maintenance jobs, with review, contradiction, stale, relearn, and eval sections
- `wiki/overviews/maintenance.md` generation during weekly hygiene
- retrieval-miss mining into `evals/candidates.jsonl` for future labeling
- test coverage for Phase 1, Phase 2, Phase 2.5, Phase 3, and Phase 4 flows

## Trust And Freshness

Phase 4 adds a trust-and-freshness layer so old knowledge does not silently
fossilize the system.

```mermaid
flowchart LR
    U["Retrieval usage"] --> T["note_trust"]
    R["Reviews + incidents + age"] --> F["note_freshness"]
    T --> X["rerank"]
    F --> X
    X --> Q["Current vs historical query handling"]
```

- trust is learned from retrieval success, usefulness labels, and failures
- freshness states are `fresh`, `aging`, `suspect`, `stale`, and `contested`
- stale notes are demoted for current-state questions
- historical queries can still pull older knowledge back when history is what you want
- MnemonicOS creates relearn tasks instead of autonomously rewriting truth

## Graph Memory

Phase 3 adds a small, SQLite-backed note graph derived only from explicit
relationships already present in canonical note frontmatter.

```mermaid
flowchart LR
    N["Canonical note"] --> R["owners / repo / linked_* / entities"]
    R --> G["graph_edges"]
    G --> X["graph-aware retrieval"]
```

- the graph is a cache, not a source of truth
- it is rebuilt during note sync, so direct vault edits stay authoritative
- it expands from the strongest focal notes instead of replacing BM25
- it helps with questions like “which decision does Sarah Chen own?” where the
  target note may not mention the owner in its body text

## Maintenance Loop

The system now closes the loop between retrieval quality and operator review.

```mermaid
flowchart LR
    Q["retrieve"] --> L["retrieval logs"]
    L --> J["daily_consolidation"]
    L --> T["trust scores"]
    J --> A["ACTIVE.md refresh"]
    J --> R["wiki/review/*.md"]
    J --> K["relearn_tasks"]
    J --> E["evals/candidates.jsonl"]
    J --> M["maintenance.md"]
    A --> H["human review"]
    R --> H
    E --> H
    K --> H
```

- unresolved review items are summarized into `brain/system/ACTIVE.md`
- stale and contested notes are surfaced with freshness state and verification dates
- contradiction reviews and relearn tasks are generated as explicit artifacts
- zero-hit or archive-only retrievals become eval candidates automatically
- dry runs report predicted backlog without mutating the vault

## Retrieval

```mermaid
flowchart LR
    Q["Query"] --> P["Pinned memory"]
    Q --> X["Exact id / alias / title"]
    Q --> B["BM25 over notes"]
    Q --> S["Semantic note search"]
    X --> G["Graph expansion from focal notes"]
    B --> G
    S --> G
    X --> R["Merge + rerank<br/>trust + freshness aware"]
    B --> R
    S --> R
    G --> R
    R --> A["Archive fallback<br/>when notes are weak"]
```

Current retrieval behavior:

- exact matches win immediately
- BM25 remains the primary retrieval backbone
- semantic retrieval is a second channel, not a replacement
- graph expansion pulls in related notes from explicit links
- reranking uses trust and freshness to separate current truth from historical memory
- archive chunk fallback is used when canonical notes are not enough

The built-in semantic encoder is intentionally simple and local. It gives
hybrid retrieval out of the box today and can later be replaced with a stronger
local embedding model without changing the vault format.

## Ingest

MnemonicOS ingests raw sessions first, then promotes only explicit or
high-confidence memories.

```mermaid
flowchart LR
    A["Session markdown"] --> B["Write raw/sessions file"]
    B --> C["Chunk + summarize"]
    C --> D["Parse memory markers"]
    D --> E{"confidence"}
    E -->|high| F["write canonical note<br/>or system memory"]
    E -->|medium| G["write review item"]
    F --> H["sync indexes"]
```

Supported Phase 2 memory markers:

````markdown
```memory
items:
  - type: decision
    title: Use PgBouncer for auth connection pooling
    body: PgBouncer will sit in front of Postgres to absorb spikes.
    tags: [auth, database]
    confidence: high
  - type: procedure
    title: Rotate auth deploy checklist
    steps:
      - Run smoke tests
      - Approve rollout
    confidence: medium
  - type: preference
    title: Prefer async PR reviews for infra changes
    confidence: high
```
````

- `high` confidence note items are written into `wiki/`
- `medium` confidence items become inspectable files in `wiki/review/`
- `preference` updates `USER.md`
- `convention` updates `MEMORY.md`
- procedures stay `draft` unless review metadata is present

## Quick Start

If installed as a package:

```bash
mnemonicos init-db
mnemonicos sync --mode full
mnemonicos ingest-session --agent codex --slug pairing --file ./notes/session.md
mnemonicos retrieve "how do we release the auth service?"
mnemonicos run-job daily_consolidation
mnemonicos run-job weekly_hygiene
```

Without installation:

```bash
PYTHONPATH=src python3 -m second_brain init-db
PYTHONPATH=src python3 -m second_brain sync --mode full
PYTHONPATH=src python3 -m second_brain ingest-session --agent codex --slug pairing --file ./notes/session.md
PYTHONPATH=src python3 -m second_brain retrieve "how do we release the auth service?"
PYTHONPATH=src python3 -m second_brain run-job daily_consolidation
PYTHONPATH=src python3 -m second_brain run-job weekly_hygiene
```

`run-job daily_consolidation` now refreshes `brain/system/ACTIVE.md`, updates trust/freshness state, creates relearn tasks, and appends retrieval misses to `evals/candidates.jsonl` on live runs.

## Repo Map

- [docs/IMPLEMENTATION_PLAN.md](/Users/jianyulong/ai_memory/docs/IMPLEMENTATION_PLAN.md): build notes and architecture details
- [migrations/001_initial_schema.sql](/Users/jianyulong/ai_memory/migrations/001_initial_schema.sql): Phase 1 schema
- [migrations/002_phase2_semantic_and_ingest.sql](/Users/jianyulong/ai_memory/migrations/002_phase2_semantic_and_ingest.sql): Phase 2 schema
- [migrations/003_phase3_graph_cache.sql](/Users/jianyulong/ai_memory/migrations/003_phase3_graph_cache.sql): Phase 3 graph schema
- [migrations/004_phase4_trust_freshness.sql](/Users/jianyulong/ai_memory/migrations/004_phase4_trust_freshness.sql): Phase 4 trust, freshness, and relearn schema
- [src/second_brain/cli.py](/Users/jianyulong/ai_memory/src/second_brain/cli.py): entry points
- [src/second_brain/ingest.py](/Users/jianyulong/ai_memory/src/second_brain/ingest.py): raw session ingest and promotion
- [src/second_brain/retrieval.py](/Users/jianyulong/ai_memory/src/second_brain/retrieval.py): hybrid retrieval
- [src/second_brain/graph.py](/Users/jianyulong/ai_memory/src/second_brain/graph.py): note graph materialization and expansion
- [src/second_brain/trust.py](/Users/jianyulong/ai_memory/src/second_brain/trust.py): trust scoring, freshness, and rerank training
- [src/second_brain/jobs.py](/Users/jianyulong/ai_memory/src/second_brain/jobs.py): maintenance jobs
- [src/second_brain/ops.py](/Users/jianyulong/ai_memory/src/second_brain/ops.py): `ACTIVE.md`, maintenance overview, and eval candidate ops

# MnemonicOS

MnemonicOS is a local-first memory system for Codex and Claude.

## Why This Design

The system is built around one rule: the markdown vault is the truth, and
everything else is a cache.

- Inspectable: humans can read and repair the vault directly.
- Portable: the full memory moves with git, not with a hosted DB.
- Recoverable: SQLite indexes, vectors, and future graph caches can be rebuilt.
- Conservative: write discipline matters more than retrieval cleverness.

## System Shape

```mermaid
flowchart TD
    A["Humans / Agents"] --> B["Markdown Vault<br/>brain/"]
    B --> C["SQLite Search Cache<br/>FTS5 + metadata"]
    B --> D["Vector Cache<br/>Phase 2"]
    B --> E["Graph Cache<br/>Phase 3+"]
    C --> F["Retriever"]
    D --> F
    E --> F
    B --> G["Pinned Memory<br/>MEMORY.md USER.md ACTIVE.md"]
    G --> F
    F --> H["LLM Context"]
```

## Memory Layers

```mermaid
flowchart LR
    L0["L0 Working Memory<br/>thread-local scratch"] --> L1["L1 Pinned Core<br/>tiny always-loaded files"]
    L1 --> L2["L2 Canonical Wiki<br/>typed markdown notes"]
    L2 --> L3["L3 Procedures<br/>draft -> active -> retired"]
    L3 --> L4["L4 Archive<br/>immutable raw sessions and docs"]
    L4 --> L5["L5 Graph / Timeline<br/>derived cache, Phase 3+"]
```

- `L1` holds durable preferences, conventions, and active focus.
- `L2` is the main long-term memory: decisions, people, projects, concepts.
- `L3` stores reusable workflows, but only after review.
- `L4` preserves raw evidence and provenance.
- `L5` is optional and only added when evals justify it.

## Vault Structure

```mermaid
flowchart TD
    brain["brain/"]
    brain --> system["system/<br/>MEMORY.md<br/>USER.md<br/>ACTIVE.md<br/>AGENTS.md"]
    brain --> raw["raw/<br/>sessions docs web_clips assets"]
    brain --> wiki["wiki/<br/>people projects repos decisions concepts incidents procedures journals overviews sources review"]
    brain --> locks["locks/"]
    brain --> data["data/<br/>archive.db vectors/ graph/"]
    brain --> templates["templates/"]
```

## Design Rationale

```mermaid
flowchart TD
    A["Problem: flat transcript memory rots"] --> B["Use typed notes"]
    B --> C["Typed notes enable metadata filters"]
    C --> D["Filters improve precision before ranking"]
    D --> E["Better recall with simpler retrieval"]

    F["Problem: embeddings are opaque"] --> G["Keep markdown as source of truth"]
    G --> H["Rebuild DB/vector/graph caches any time"]

    I["Problem: stale facts outrank current truth"] --> J["Use temporal fields<br/>valid_from valid_to last_verified_at"]
    J --> K["Prefer current, verified notes"]
```

- Typed notes beat flat logs for recall and maintenance.
- Exact lookup plus BM25 is the Phase 1 baseline; semantic and graph layers are
  additive, not foundational.
- Procedures are gated because a bad workflow note poisons future behavior.
- Consolidation is a first-class service so duplicates and alias drift do not
  quietly degrade retrieval.

## Write Path

```mermaid
flowchart LR
    A["Raw session/doc"] --> B["Save to raw/"]
    B --> C["Chunk + archive"]
    C --> D["Extract candidates"]
    D --> E{"Promotion filter"}
    E -->|No| F["Archive only"]
    E -->|Yes| G["Upsert canonical note<br/>or create review item"]
    G --> H["Refresh SQLite search rows"]
    H --> I["Log change"]
```

## Retrieval Path

```mermaid
flowchart LR
    A["Query"] --> B["Pinned memory"]
    A --> C["Exact alias / title / id"]
    A --> D["BM25 over canonical notes"]
    A --> E["Semantic search<br/>Phase 2"]
    C --> F["Merge + rerank"]
    D --> F
    E --> F
    F --> G["Optional archive fallback"]
    G --> H["Compact context for the model"]
```

Phase 1 implements the bold path here: pinned memory, exact lookup, and BM25.

## What Phase 1 Builds

```mermaid
flowchart TD
    A["Migrations"] --> B["SQLite database"]
    B --> C["Vault sync"]
    C --> D["notes / aliases / notes_search"]
    D --> E["FTS5 retrieval"]
    E --> F["CLI: init-db, sync, retrieve"]
```

- migration runner
- markdown frontmatter parser
- incremental vault sync into SQLite
- exact alias/title/id retrieval
- BM25 retrieval over canonical notes

## Running Phase 1

If installed as a package:

```bash
mnemonicos init-db
mnemonicos sync --mode full
mnemonicos retrieve "why did we pick postgres for auth?"
```

Without installation, the current module path is still `second_brain`:

```bash
PYTHONPATH=src python3 -m second_brain init-db
PYTHONPATH=src python3 -m second_brain sync --mode full
PYTHONPATH=src python3 -m second_brain retrieve "why did we pick postgres for auth?"
```

Implementation details live in [docs/IMPLEMENTATION_PLAN.md](/Users/jianyulong/ai_memory/docs/IMPLEMENTATION_PLAN.md).

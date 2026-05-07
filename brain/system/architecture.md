# MnemonicOS Architecture

This is the governing index for MnemonicOS architecture documentation.

## Authority Order

When documents conflict, use this order:

1. Current governing files under `brain/system/`.
2. Current runtime/API implementation and tests.
3. Strategic roadmaps under `development/`.
4. Historical or obsolete docs marked `OBSOLETE`.

The west-flange and LLM/VLM governing plans are already authoritative and should
not be rewritten as part of this cleanup.

## Current System Shape

```mermaid
flowchart TD
    A["North callers<br/>Agent / CLI / Hook / HTTP / human"] --> B["North adapter layer<br/>orchestrator.py + server.py + scripts<br/><b>software-driven</b>"]

    B --> C["Runtime layer<br/>runtime.py<br/><b>software-driven</b>"]
    C --> D["East retrieval layer<br/>retrieval.py + trust.py + graph.py<br/><b>software-driven today</b>"]
    D --> E["Bounded memory packet<br/>recommendation + warnings + provenance<br/><b>software-driven</b>"]
    E --> A

    B --> F["West intake layer<br/>ingest.py + capture + connectors<br/><b>software-driven</b>"]
    F --> G["Immutable true raw archive<br/>brain/raw/*<br/><b>software-owned witness material</b>"]
    G --> H["RAM-only usable raw projection<br/>ProjectionLease + TextWindow + ArtifactView<br/><b>software-driven</b>"]

    I["Daemon + scheduled jobs<br/>daemon.py + daemon_jobs.py + jobs.py<br/><b>software-driven orchestration</b>"] --> H
    I --> D

    H --> J["VLM visual snippet layer<br/>figures / media -> descriptions<br/><b>AI-driven</b>"]
    H --> K["LLM extraction layer<br/>text + visual snippets -> candidate JSON<br/><b>AI-driven</b>"]
    J --> K
    K --> L["Candidate validation<br/>schema + provenance + binding checks<br/><b>software-driven</b>"]
    L --> M["Promotion-ready candidates<br/>extraction_candidates<br/><b>software-owned state</b>"]
    M --> N["Deterministic promotion layer<br/>create / update / confirm / review<br/><b>software-driven; plan missing</b>"]

    N --> O["Canonical wiki<br/>brain/wiki/*<br/><b>software/human governed memory</b>"]
    O --> P["SQLite/index substrate<br/>FTS + vectors + graph + state<br/><b>software-driven derived stores</b>"]
    P --> D

    Q["Future learned retrieval core<br/>fetch + sort relevant wiki/context<br/><b>AI-driven, read-only, no mutation</b>"] -.-> D
    P -.-> Q
    O -.-> Q
```

The storage/index substrate is currently SQLite plus markdown, FTS, vectors, and
graph/cache tables. Whether this should be treated as a full south flange or a
lower-level replaceable substrate remains an open architecture question.

## Governing References

- [Product Boundary](product-boundary.md): product scope, flange vocabulary, and
  current boundary rules.
- [West-Flange Development Plan](west-flange-development-plan.md): true raw,
  artifact identity, RAM projection, text windows, artifact views, and extraction
  handoff.
- [West-Flange Development Roadmap](west-flange-development-roadmap.md): current
  west-flange implementation status and remaining verification gaps.
- [LLM/VLM Layer Development Plan](llm-vlm-layer-development-plan.md): extraction
  queue, VLM snippets, LLM candidate bundles, deterministic validation, and the
  stop at `promotion_ready`.
- [LLM/VLM Layer Development Roadmap](llm-vlm-layer-development-roadmap.md):
  current extraction-layer implementation status and remaining work.
- [Bridge Capture Development Plan](bridge-capture-development-plan.md):
  north-flange raw session capture model.

## Known Missing Governing Plans

These are intentionally not filled in by this cleanup:

- deterministic promotion layer plan;
- east-flange retrieval and future learned retrieval-core plan;
- library RPG interface skeleton;
- storage/substrate or south-boundary decision note.

## Obsolete Or Historical Planning Docs

The following docs are retained only for historical context and should not define
current implementation work:

- [Old docs boundary pointer](../../docs/boundary.md)
- [Old implementation plan](../../docs/IMPLEMENTATION_PLAN.md)
- [Old VLM/LLM extraction daemon plan](../../docs/vlm-llm-extraction-daemon-plan.md)
- [Old daemon deferred promotion roadmap](../../development/roadmap-daemon-deferred-promotion.md)
- [Old promotion detach transition plan](promotion-detach-plan.md)

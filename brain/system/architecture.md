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
    C --> D["Main ingestion compiler<br/>west intake -> projection -> extraction candidates<br/><b>mixed: software orchestration + AI extraction</b>"]
    D --> E["Deterministic promotion layer<br/>create / update / confirm / review<br/><b>software-driven; plan missing</b>"]
    E --> F["Canonical memory store<br/>brain/wiki/* + SQLite/index substrate<br/><b>software/human governed + derived stores</b>"]
    F --> G["East retrieval layer<br/>retrieval.py + trust.py + graph.py<br/><b>software-driven today</b>"]
    G --> H["Bounded memory packet<br/>recommendation + warnings + provenance<br/><b>software-driven</b>"]
    H --> A

    subgraph W["West ingestion detail"]
        W1["Raw intake<br/>ingest.py + capture + connectors<br/><b>software-driven</b>"]
        W2["Immutable true raw archive<br/>brain/raw/*<br/><b>software-owned witness material</b>"]
        W3["RAM-only usable raw projection<br/>ProjectionLease + TextWindow + ArtifactView<br/><b>software-driven</b>"]
        W1 --> W2 --> W3
    end

    subgraph X["AI extraction detail"]
        X1["VLM visual snippets<br/>figures / media -> descriptions<br/><b>AI-driven</b>"]
        X2["LLM candidate bundle<br/>text + visual snippets -> candidate JSON<br/><b>AI-driven</b>"]
        X3["Candidate validation<br/>schema + provenance + binding checks<br/><b>software-driven</b>"]
        X4["Promotion-ready candidates<br/>extraction_candidates<br/><b>software-owned state</b>"]
        X1 --> X2 --> X3 --> X4
    end

    D -.->|breakdown| W
    W3 --> X1
    W3 --> X2
    X4 -.->|feeds| D

    I["Daemon + scheduled jobs<br/>daemon.py + daemon_jobs.py + jobs.py<br/><b>software-driven orchestration</b>"] --> D
    I --> G

    Q["Future learned retrieval core<br/>fetch + sort relevant wiki/context<br/><b>AI-driven, read-only, no mutation</b>"] -.->|future read-only rerank/fetch| G
    F -.-> Q
```

The storage/index substrate is currently SQLite plus markdown, FTS, vectors, and
graph/cache tables. Whether this should be treated as a full south flange or a
lower-level replaceable substrate remains an open architecture question.

## Governing References

### Ingestion Pipeline (West Flange + Extraction)

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

### Next Layer (Stub Plans)

- [Promotion Layer Plan](promotion-layer-plan.md): stub for the deterministic
  promotion layer — consumes `promotion_ready` candidates and writes canonical
  wiki notes.
- [East Flange Plan](east-flange-plan.md): current retrieval state and stub for
  the future learned retrieval core.

### Runtime Protocol and Agent Bridges

- [Runtime Protocol](runtime-protocol.md): canonical protocol contract for
  `resolve_task_context`, `record_outcome`, `decide_writeback`, `run_maintenance`.
- [Claude Code Bridge](claude-bridge.md): hook wiring, task/session identity,
  writeback policy, enable/disable switch. Both phases implemented.
- [Codex Bridge](codex-bridge.md): current skill-based path and planned Phase 2
  adaptation layer (MCP, wrapper, breakpoint checkpointing).

### Retrieval and Quality

- [Control Signal Quality Roadmap](control-signal-quality-roadmap.md): ACTIVE —
  gene strings, content quality gate, chain_id machinery.
- [Episode-Aware Retrieval](episode-aware-retrieval.md): Phase 1 done (chain
  metadata on notes); Phases 2–6 deferred until evaluation data exists.

### Interface and Positioning

- [Game Interface Skeleton](game-interface-skeleton.md): non-negotiable design
  decisions for the library RPG interface.
- [Competitive Positioning](competitive-positioning.md): positioning statement,
  differentiators, and market context.

### Vision and Roadmap

- [Humanoid MnemonicOS Core Blueprint](../../development/humanoid-MnemonicOS-core-blueprint.md):
  grand architectural vision — epistemic metabolism, provenance, abstraction
  distance, learned reasoning.
- [Learned Daemon Roadmap](../../development/learned-daemon-and-true-raw-memoryos-roadmap.md):
  Step 0 (MnemonicOS as MemoryOS), Step 1 (learned daemon), Step 2 (neuro-symbolic
  core).
- [Library RPG Roadmap](../../development/roadmap-library-rpg.md): detailed
  implementation plan for the game interface.

## South Boundary

The storage/index substrate (SQLite, vectors, graph) is currently treated as
replaceable substrate behind MnemonicOS. Whether to formalize this as a "south
flange" with explicit adapter contracts remains an open architecture question.

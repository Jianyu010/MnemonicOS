# MnemonicOS Product Boundary

This document is the canonical product-boundary reference for MnemonicOS.

MnemonicOS is a local-first, agent-independent memory runtime. It owns the
memory contract and lifecycle machinery around true raw intake, projection,
candidate extraction, promotion policy, retrieval packets, outcome feedback,
trust, freshness, review, and daemon-owned maintenance.

The south-flange boundary is still under consideration. For now, storage and
index backends remain replaceable substrate behind MnemonicOS rather than the
product itself.

## What MnemonicOS Is

MnemonicOS owns:

- typed memory taxonomy and governed canonical wiki material;
- true-raw archive intake and provenance preservation;
- MnemonicOS-owned projection plugins for sessions, PDFs, HTML, DOCX, PPTX,
  Markdown, plain text, logs, web clips, code docs, copied notes, and related
  raw material;
- extraction queue and validated VLM/LLM candidate records;
- deterministic promotion policy from validated candidates into canonical wiki;
- retrieve -> outcome -> writeback feedback loop;
- policy-aware retrieval packets, not just ranked hits;
- trust, freshness, contradiction, and review handling;
- daemon-owned maintenance over replaceable storage and index backends.

## What MnemonicOS Is Not

### Not A Database

SQLite, vector indexes, graph stores, object stores, and future database choices
are replaceable substrates underneath MnemonicOS. MnemonicOS owns the lifecycle,
policy, provenance, and serving semantics above those stores.

### Not An Agent Or Agent Framework

MnemonicOS is called by agents; it does not call models to produce task output
for an agent. Agent runtimes such as Codex, Claude Code, OpenClaw, CLI, HTTP,
or future MCP clients sit on top as interface layers.

The north-facing contract includes operations such as `begin_task`,
`mark_failure`, `finish_task`, `resolve_task_context`, `record_outcome`,
`decide_writeback`, and `run_maintenance`.

### Not A Memory Framework Library

Framework-embedded memory helpers are libraries imported into one agent process.
MnemonicOS is a runtime with a stable local contract and a user-owned vault. A
framework can call MnemonicOS without replacing the framework.

### Not An Embeddings Service

MnemonicOS may consume embeddings, but embedding model choice, batch policy, and
backfill are implementation details behind replaceable storage/index adapters.

### Not A General-Purpose Document Converter

The west flange owns raw intake and MnemonicOS-owned projection plugins, but
MnemonicOS should not become a standalone file-conversion product. It archives
true raw first, then regenerates temporary usable raw projections for extraction.

### Not A Workflow Engine

MnemonicOS scheduled jobs are memory housekeeping: extraction, promotion,
maintenance, backfill, consolidation, review, and status reporting. General
workflow orchestration belongs outside MnemonicOS and calls MnemonicOS through
the runtime contract.

## Flange Model

The current architectural vocabulary uses three stable flanges and one tentative
substrate boundary:

- **West**: raw material intake and MnemonicOS-owned projection.
- **East**: retrieval and context delivery.
- **North**: agent, human, CLI, HTTP, and future MCP adapters.
- **South / substrate boundary**: storage and index adapters. This may remain a
  lower-level substrate boundary rather than a full product flange.

## Current Data Flow

```text
raw source
  -> west-flange true raw archive
  -> RAM-only usable raw projection
  -> VLM/LLM extraction candidates
  -> deterministic promotion policy
  -> canonical wiki + indexes
  -> east retrieval packets
  -> north callers
```

## Boundary Rules

- Raw authority stays inside MnemonicOS.
- True raw is immutable witness material, not canonical truth.
- Usable raw is RAM-only projection and is not normal retrieval context.
- VLM descriptions and LLM interpretations are extraction-layer records.
- Wiki writes happen only through deterministic promotion policy or deliberate
  human/manual maintenance.
- Normal retrieval reads canonical notes and indexes, not true raw, usable raw,
  or unvalidated extraction candidates.
- Adapter churn stays behind flanges and must not redefine the core memory
  contract.

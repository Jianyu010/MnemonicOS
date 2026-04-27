# MnemonicOS Product Boundary

This document defines what MnemonicOS is by being explicit about what it is **not**. The boundary matters because memory-adjacent projects in the LLM ecosystem often collapse multiple concerns into one library or product; MnemonicOS picks a narrow runtime layer on purpose.

See [development/roadmap-mnemonicos-competitive-positioning.md](../development/roadmap-mnemonicos-competitive-positioning.md) for the full competitive analysis and flange definitions.

## What MnemonicOS Is

MnemonicOS is a **local-first, agent-independent memory runtime**. It owns the memory contract and lifecycle machinery:

- typed memory taxonomy (source, procedure, decision, incident, concept)
- true-raw archive intake and provenance preservation
- MnemonicOS-owned projection plugins for raw artifacts such as sessions, PDFs, HTML, DOCX, Markdown, plain text, web clips, and code docs
- promotion queue and typed promotion policy
- retrieve → outcome → writeback feedback loop
- policy-aware retrieval packets (not just ranked hits)
- trust, freshness, contradiction handling
- daemon-owned maintenance over pluggable storage

## What MnemonicOS Is Not

### Not A Database

SQLite, vector indexes, graph stores, and object stores are **replaceable substrates** underneath MnemonicOS. Markdown remains the source of truth; derived stores rebuild from it. MnemonicOS owns the lifecycle and policy semantics that live over those stores. Swapping the vector index or graph engine is a configuration change, not a product pivot.

### Not An Agent Or Agent Framework

MnemonicOS is called by agents; it does not call models on its own to produce task output. Agents like Claude Code, Codex, OpenClaw, or any HTTP/MCP client sit on top as interface layers. The `north flange` defines the agent adapter contract — `begin_task`, `mark_failure`, `finish_task`, `resolve_task_context`, `record_outcome`, `decide_writeback`, `run_maintenance` — and nothing in that list requires MnemonicOS to orchestrate an LLM loop.

### Not A Memory Framework (Library)

Framework-embedded memory helpers — LangMem, LangChain memory classes, LlamaIndex memory modules, Agent SDK memory helpers — are libraries you import into one agent's process. MnemonicOS is a **runtime** you send requests to over a stable contract. That difference has real consequences:

- Framework memory scopes to the framework's data model; MnemonicOS scopes to the user's local vault with agent-scoped identity.
- Framework memory upgrades when the framework upgrades; MnemonicOS upgrades on its own cadence.
- A framework user should be able to call MnemonicOS from inside their framework code without replacing the framework.

### Not An Embeddings Service

MnemonicOS consumes embeddings, it does not produce them. Model choice, batch policy, and backfill are implementation concerns under the `south flange`. Swapping embedding providers does not change the MnemonicOS contract.

### Not A General-Purpose Document Converter

MnemonicOS should not become a generic file-conversion product. However, the west flange is part of MnemonicOS, and it must own the raw intake contract.

MnemonicOS accepts true raw artifacts first: full agent sessions, PDFs, HTML pages, DOCX files, Markdown, plain text, web clips, logs, code documents, and copied notes. These artifacts are archived immutably before any interpretation. Official west-flange plugins may then derive normalized projections such as extracted text, chunks, page ranges, metadata, spans, entities, or candidate source records. Those projections are rebuildable derivatives, not replacements for raw.

Agents and external tools may submit raw artifacts and optional metadata, but they should not be required to trim, summarize, reformat, or "clean" material before MnemonicOS receives it. If upstream agents pre-process everything into a rigid convention, MnemonicOS becomes a skill protocol rather than a memory system.

### Not A Workflow Engine

MnemonicOS does not orchestrate multi-step business workflows. Scheduled jobs inside the daemon (promotion, maintenance, backfill) are internal housekeeping, not a general-purpose workflow surface. If you need workflow orchestration, that system calls MnemonicOS.

## Why The Boundary Is Narrow On Purpose

Every feature added inside the boundary competes with something specialized outside it. Keeping the runtime narrow means:

1. **Raw authority stays inside MnemonicOS.** External agents and tools can hand over raw material, but MnemonicOS preserves the unmodified witness artifact and controls the derived projections.
2. **Adapter churn stays behind flanges.** New document parsers, vector stores, and agents plug into a flange without changing the core memory contract.
3. **The contract is small enough to be trustworthy.** Agents and tools can rely on MnemonicOS because its surface is well-defined.
4. **Competitive collisions stay on positioning, not on product scope.** When another project "adds memory," MnemonicOS either consumes its raw outputs, serves it governed memory, or slots underneath it.

## Flange Summary

- **North**: agent and runtime adapters (Codex, Claude Code, OpenClaw, CLI, HTTP, MCP).
- **South**: storage and index adapters (SQLite, vector, graph, object store).
- **East**: retrieval and context delivery (policy-aware packets).
- **West**: true raw material intake and MnemonicOS-owned projection plugins (agent sessions, PDFs, HTML, DOCX, Markdown, plain text, web clips, logs, code docs, copied notes).

Anything inside the four flanges is MnemonicOS. Anything outside is a replaceable component.

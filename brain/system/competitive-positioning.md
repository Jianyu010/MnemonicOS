# MnemonicOS Competitive Positioning And Roadmap

Date: 2026-04-20

Context: This plan is based on `MnemonicOS_Competitive_Landscape.pdf`, repo inspection, and follow-up source checks on current LLM memory projects. It preserves the positioning and roadmap decisions from the competitive review so the work can be revisited and implemented later.

## Positioning Statement

Short pitch line:

> MnemonicOS is the memory contract an agent can trust, regardless of who built the agent.

Long technical statement:

> Local-first, agent-independent memory runtime for agents: typed promotion, outcome feedback, policy-aware retrieval packets, and daemon-owned maintenance over pluggable storage and ingestion surfaces.

The competitive correction is important: slow-path promotion is no longer unique. Honcho's Dreaming and OpenClaw's Dreaming / `DREAMS.md` make that overlap real, not just terminology. The safer moat is the combination of typed memory lifecycle, retrieve/outcome/writeback feedback, policy-aware context packets, and daemon-owned maintenance.

Use "agent-independent, agent-facing" instead of "agent-free." Agent-free can imply no agents are involved, while the real claim is that MnemonicOS works with any agent runtime without being owned by one.

### Dimension-Level Differentiators

Feature-level parity with a competitor is easy to lose; dimension-level differentiation is not. The three dimensions below are load-bearing because verified 2026-04 research shows no surveyed competitor covers them:

1. **Double-linked provenance.** Zep/Graphiti links facts to episodes. MnemonicOS links typed notes to raw-source spans **and** to success/failure traces. Evidence and validation are both first-class.
2. **Negative knowledge as a first-class primitive.** The outcome loop captures `misleading_memory`, incidents, demotion signals, and repeated-failure escalations. Competitors store what worked; MnemonicOS also stores what misled.
3. **Cross-agent shared vault with agent-scoped identity.** Codex, Claude Code, OpenClaw, and future agents feed one local vault with `agent_id` scoping, audit trails, and bridge permissions. No surveyed competitor supports this natively.

### What MnemonicOS Is Not

The boundary definition is part of the positioning. MnemonicOS is:

- Not a **database**. It uses databases as replaceable storage substrates.
- Not an **agent** or an **agent framework**. It is called by agents.
- Not a **memory framework** (library you import). It is a runtime (service you send requests to).
- Not an **embeddings service**. It consumes one.
- Not a **general-purpose document converter**. It owns the true-raw intake contract and official projection plugins, but does not try to compete as a standalone file-conversion product.
- Not a **workflow engine**. It is called by one.

See also [brain/system/product-boundary.md](../brain/system/product-boundary.md)
for the canonical negative-space definition.

## Competitive Label Rubric

Replace the broad `partial` label with more precise meanings:

- **Structural partial**: the competitor has a similar primitive, but not the same policy, lifecycle, or operating boundary.
- **Terminology partial**: the competitor uses different words for nearly the same mechanism.
- **Scope partial**: the competitor does it for users, conversations, or personalization, but not for agent-facing procedural knowledge.
- **Operational partial**: the competitor documents it, but implementation, integration, or reliability appears incomplete.

This rubric should be applied anywhere the competitive matrix currently uses `~`.

## Revised Competitor Assessments

### Honcho

Slow-path consolidation is meaningfully real, not only similar terminology. Honcho's Dreaming performs autonomous consolidation, contradiction handling, updates, confidence-bearing induction, scheduling, idle delay, and peer-scoped memory refinement.

Implication: do not present MnemonicOS slow promotion alone as unique.

Differentiation:

- MnemonicOS has typed procedural and operational knowledge, not primarily social/user modeling.
- MnemonicOS links retrieval to task outcomes and writeback decisions.
- MnemonicOS keeps Markdown as a rebuildable source of truth.
- MnemonicOS returns policy-aware context packets rather than only context or search output.

Source checked: Honcho Dreaming docs.

### OpenClaw

Promotion and review are meaningfully real. OpenClaw has `DREAMS.md`, short-term dreaming storage, deep promotion into `MEMORY.md`, grounded backfill, and a Memory Wiki layer with claims, evidence, contradiction/freshness tracking, dashboards, and wiki tools.

Implication: treat OpenClaw as strong overlap in promotion and wiki direction.

Differentiation:

- MnemonicOS is the memory runtime itself, not a plugin beside an active memory owner.
- MnemonicOS has explicit runtime operations for resolve context, record outcome, decide writeback, and maintenance.
- MnemonicOS has typed note families and policy-aware packet semantics as the main contract.
- MnemonicOS can define adapter boundaries more cleanly across agents, storage engines, raw-material intake, and retrieval surfaces.

Source checked: OpenClaw memory docs.

#### OpenClaw Positioning Stance

Research on 2026-04-20 surfaced strong terminology isomorphism between OpenClaw and MnemonicOS (DREAMS.md, MEMORY.md, memory-wiki with claims and evidence, light/REM/deep dreaming phases). Architectures diverge below the surface — verified: OpenClaw has no typed families, no outcome-linked feedback, no PDF/HTML/DOCX intake, and is tightly bound to a personal assistant product. But the naming collision is real and could cost positioning clarity.

Chosen stance (default — revisit before any external materials ship):

> **MnemonicOS is the typed-lifecycle memory runtime that an OpenClaw-style assistant can sit on top of.** OpenClaw is a product; MnemonicOS is infrastructure. They operate at different layers.

Write this stance down in one place (this roadmap), and mirror it in the README positioning paragraph and any future pitch materials. If the stance changes, update all three together.

### Mem0 / OpenMemory

Structured memory types should move from absent to partial. OpenMemory advertises memory types, project/repo scoping, audit logs, visibility rules, automatic capture, and automatic context delivery.

Implication: do not claim that Mem0/OpenMemory has only flat facts without qualification.

Differentiation:

- Its typed organization appears aimed at coding preferences and project context, not a full lifecycle model for decisions, procedures, incidents, sources, and outcome-linked promotion.
- MnemonicOS can own the policy layer: when memory is safe to reuse, when to verify, when to research fresh, and when to update canonical knowledge.
- Mem0 has a lower adoption-friction story because it auto-captures; MnemonicOS should respond with passive raw intake and daemon promotion.

Sources checked: Mem0/OpenMemory site and Mem0 paper summary.

### Zep / Graphiti

Structured types and raw intake are partial but strong. Graphiti supports custom entity types, episodic processing, temporal edges, hybrid search, and unstructured plus structured JSON ingestion.

Implication: do not position MnemonicOS as merely "graph memory with timestamps."

Differentiation:

- Zep/Graphiti is a temporal graph context layer.
- MnemonicOS should be a wiki/policy runtime with procedure, decision, incident, source, and outcome-aware lifecycle governance.
- Zep/Graphiti is stronger for production graph retrieval and live temporal reasoning; MnemonicOS needs better embeddings before any external retrieval demo.

Source checked: Graphiti overview.

### A-Mem

Structured notes, dynamic linking, and memory evolution are real partial overlaps. A-Mem generates comprehensive notes with contextual descriptions, keywords, tags, links to historical memories, and updates to existing representations.

Implication: treat A-Mem as philosophical and research overlap around note evolution and dynamic organization.

Differentiation:

- A-Mem is an agentic organization research system.
- MnemonicOS is a local daemon/service/runtime with explicit agent protocol, promotion queues, durable vault, and maintenance jobs.
- A-Mem's forgetting and dynamic reorganization remain useful design references for MnemonicOS.

Source checked: A-Mem paper page.

### MemOS

MemOS should not be treated as a direct feature collision. It uses broader memory-OS framing across parametric, activation, and plaintext memory via MemCube and memory scheduling/governance.

Implication: avoid claiming "memory OS" generically as the main novelty.

Differentiation:

- MemOS targets memory as a system resource across model memory forms.
- MnemonicOS should claim a narrower runtime boundary: local-first agent memory lifecycle, not model/activation/parameter memory scheduling.
- MnemonicOS can still borrow vocabulary around governance, lifecycle, and scheduling, but should be explicit that it operates at the plaintext/canonical knowledge runtime layer.

Source checked: MemOS paper page.

### Evolver / EvoMap

Overlap exists in self-evolution framing, hooks, daemon loops, offline/network modes, memory files, and capability assets. The center of gravity is evolution prompt generation and capability reuse, not canonical memory governance.

Implication: avoid conflating Evolver with a memory backend, but monitor it because its capability-inheritance framing could overlap with MnemonicOS bridge permissions and cross-agent procedural knowledge.

Differentiation:

- Evolver is a prompt generator and self-evolution engine.
- MnemonicOS is the memory runtime and governance layer.
- MnemonicOS should define clear flanges so systems like Evolver can be callers or downstream consumers rather than direct competitors.

Source checked: Evolver README.

### Framework-Embedded Memory Abstractions

The most common alternative users reach for is not a dedicated memory project — it is a memory helper inside the agent framework they already use: LangMem, LangChain memory classes, LlamaIndex memory modules, Agent SDK memory helpers, CrewAI memory, and similar.

Real partial type: **scope partial (c)** and **operational partial (d)** — these are in-process libraries scoped to one agent's conversation state, with lifecycle depth that rarely exceeds conversation summarization and vector recall. Most do not persist typed procedural/decision/incident knowledge across sessions, do not link retrieval to task outcomes, and do not accept raw documents outside of the framework's ingestion path.

Differentiation:

- Framework memory = library imported into one agent's process. MnemonicOS = runtime called over a stable contract from any agent.
- Framework memory scopes to the framework's data model. MnemonicOS scopes to the user's local vault with agent-scoped identity.
- Framework memory upgrades when the framework upgrades. MnemonicOS upgrades on its own cadence.

Implication: position MnemonicOS in a **different product category**, not as a competitor inside the same category. A LangChain or Agent SDK user should be able to call MnemonicOS from inside their framework code without replacing the framework.

Sources checked: LangMem/LangChain memory, LlamaIndex memory, Agent SDK memory docs (as of 2026-04-20).

### Letta (MemGPT) And cognee

Letta (previously MemGPT) and cognee are other dedicated memory projects worth a one-line stance each so the memo is complete.

- **Letta / MemGPT** — agent-integrated memory with hierarchical archival/recall memory and self-editing core memory; agent-coupled (MemGPT is an agent runtime, not just memory). Real partial type: **scope partial (c)** — owns the agent loop, not an agent-independent contract. MnemonicOS is the complement if the user wants to keep their own agent.
- **cognee** — knowledge-graph-oriented memory with document ingestion and graph-backed retrieval. Real partial type: **structural partial (a)** — strong on graph/ingestion, weak on typed lifecycle, outcome loop, and policy-aware packets. Worth tracking if it adds lifecycle governance later.

Sources checked: Letta and cognee READMEs (as of 2026-04-20).

## Product Boundary And Flanges

MnemonicOS should be defined as the central memory machinery. It is not the database, not the agent, and not the only retrieval engine.

### North Flange: Agent And Runtime Adapters

Connectors:

- Codex
- Claude Code
- OpenClaw
- CLI
- HTTP
- MCP or future local protocol adapters

Contract:

- `begin_task`
- `mark_failure`
- `finish_task`
- `resolve_task_context`
- `record_outcome`
- `decide_writeback`
- `run_maintenance`

The agent adapter should not own memory semantics. It should pass stable identity, task, session, scope, outcome, and writeback data into MnemonicOS.

### South Flange: Storage And Index Adapters

Markdown remains the source of truth.

Replaceable derived stores:

- SQLite
- vector indexes
- graph databases
- object stores
- external databases for specific functional groups

Rule: databases are implementation substrates, not the product boundary. MnemonicOS owns the lifecycle and policy semantics over those stores.

### West Flange: True Raw Material Intake

Raw inputs:

- agent sessions
- PDFs
- HTML
- Word documents
- Markdown
- plain text
- web clips
- code docs
- copied notes
- logs and command traces

Contract:

- accept true raw artifacts without requiring upstream agents or tools to trim, summarize, or reshape them
- archive raw material immutably as witness material
- compute metadata and content identity for the original artifact where possible
- create MnemonicOS-owned projections such as extracted text, chunks, spans, page ranges, session turns, and source candidates
- normalize projections into source records
- extract claims, entities, procedures, decisions, incidents, and concepts
- enqueue candidates for typed promotion
- preserve provenance from canonical notes back to raw material

Design rule:

- Raw is comprehensive and may be redundant or noisy by design. Filtering belongs in projection, promotion, retrieval, and serving policy, not before archival. Agents can submit raw and optional metadata, but if every upstream tool must first follow a rigid "memory skill" format, MnemonicOS has stopped being the memory system and has become only a convention for agents.

### East Flange: Retrieval And Context Delivery

MnemonicOS should return policy-aware packets, not raw ranked hits.

Packet semantics:

- retrieval state, such as `sufficient_current`, `sufficient_historical`, `partial`, `insufficient_stale`, or `insufficient_no_hit`
- recommendation, such as `reuse`, `reuse_with_verification`, or `research_fresh`
- verification action
- primary memories
- same-episode context
- related notes by topic, timeline, similarity, and contrast
- warnings and provenance

### Core Machinery

Core responsibilities:

- typed memory taxonomy
- archive-first intake
- promotion queue and promotion policy
- outcome feedback
- trust and freshness scoring
- contradiction handling
- daemon-owned maintenance
- governance, audit, and bridge permissions

## Roadmap

### Phase 1: Competitive Memo Correction

Update the competitive document so it no longer overclaims slow-path promotion as unique.

Actions:

- Replace every broad `partial` label with structural, terminology, scope, or operational partial.
- Add a collision-risk field per dimension:
  - promotion
  - taxonomy
  - raw intake
  - trust/freshness
  - outcome feedback
  - policy packets
  - governance
  - local-first rebuildability
- Reframe Honcho and OpenClaw as meaningful promotion overlaps.
- Reframe Mem0/OpenMemory as partial typed memory plus strong adoption-friction competitor.
- Reframe Zep/Graphiti as strong temporal graph and ingestion competitor.
- Change "agent-free" to "agent-independent, agent-facing."

Deliverable:

- updated board memo / competitive matrix
- short positioning paragraph for README or pitch materials

### Phase 2: Taxonomy v2

Keep the current note kinds, but refine them into memory families before adding new folders or excessive note types.

Memory families:

- **Material/source memory**: what, when, who, where, why, how, extracted claims, entities, and provenance.
- **Procedural memory**: goal, prerequisites, applicability, steps, failure modes, success criteria, rollback, verification.
- **Decision memory**: decision, rationale, alternatives, validity window, owner, supersession.
- **Incident memory**: symptom, cause, fix, prevention, linked procedure, linked decision.
- **Concept/entity memory**: definitions, claims, evidence, relations, validity.

Actions:

- Strengthen templates for `source`, `procedure`, `decision`, `incident`, and `concept`.
- Add validation rules before adding new durable types.
- Preserve compatibility with current folders and note IDs.
- Keep `overview` and `journal` as synthesis/history surfaces, not core factual types.

Deliverable:

- taxonomy v2 spec
- updated templates
- parser/validator changes

#### Phase 2 Implementation Note: Field Schemas As Contract

Taxonomy v2 should ship as YAML field schemas, not prose descriptions. One schema file per family lives under `brain/system/schemas/`:

- `source.yaml` — what, when, why, who, where, how, how-to, extracted claims, entities, provenance fields.
- `procedure.yaml` — goal, prerequisites, applicability, steps, failure modes, success criteria, rollback, verification.
- `decision.yaml` — decision, rationale, alternatives, validity window, owner, supersession.
- `incident.yaml` — symptom, cause, fix, prevention, linked procedure, linked decision.
- `concept.yaml` — definition, claims, evidence, relations, validity.

Validators reject notes missing required fields. Templates are generated from the schema (single source of truth). This prevents prose-level drift and makes promotion policy (Phase 4) cleanly typed.

### Phase 2.5: Policy-Aware Retrieval Packet Hardening

The most verified competitive moat (2026-04-20 research) is that no surveyed competitor returns policy-aware retrieval packets — all return ranked hits. MnemonicOS already emits the `## Memory Context` block with `retrieval_state` and `recommendation` fields. Hardening this before large taxonomy and intake work lands gives the project a demo-able moat and forces downstream phases to emit packet-ready metadata.

Actions:

- Formalize the packet schema: `retrieval_state`, `recommendation`, `verification_action`, `primary_memories`, `same_episode_context`, `related_notes`, `warnings`, `provenance`.
- Publish the packet schema as a versioned contract document under `api/` or `docs/`.
- Add test fixtures covering each `retrieval_state`: `sufficient_current`, `sufficient_historical`, `partial`, `insufficient_stale`, `insufficient_contested`, `insufficient_no_hit`.
- Ensure `verification_action` is populated and actionable (not placeholder text) when recommendation is `reuse_with_verification` or `research_fresh`.
- Add evaluation slice: measure agent task success rate with vs. without packet fields exposed to the caller.

Deliverable:

- versioned packet schema document
- fixtures and tests per retrieval state
- eval slice demonstrating packet value

### Phase 3: Universal True-Raw Intake Daemon

Add a raw-material intake pipeline that can accept full agent sessions, PDFs, HTML, Word docs, Markdown, plain text, logs, web clips, code docs, and copied notes as true raw artifacts.

Actions:

- Add an intake command/API for raw artifacts, distinct from session-only ingest.
- Store original raw files immutably under `brain/raw/*`.
- Add official west-flange projection plugins for the important raw classes: session transcript, PDF, HTML, DOCX, Markdown/plain text, logs, and code docs.
- Store projections separately from raw artifacts so they can be regenerated as extraction improves.
- Create `source` candidates for every imported material.
- Extract candidate concepts, procedures, decisions, incidents, and claims.
- Enqueue extracted candidates into the promotion queue rather than writing canonical notes directly.
- Preserve provenance links from every candidate back to raw source and, where possible, extracted spans or sections.

Important design rule:

- Raw intake should be passive and agent-independent. A user should be able to dump documents and full sessions into MnemonicOS and let the daemon organize them without teaching every agent a special protocol. Agents may provide metadata, but they should not be responsible for deciding what the raw material is allowed to contain.

Ordering rule (R5): **Phase 4's promotion policy spec must be frozen before Phase 3 implementation code lands.** Candidate shape depends on promotion policy; otherwise intake emits the wrong fields and the first promotion pass triggers rework. Implementation order stays intake → promotion, but the contract precedes both.

Self-host acceptance (R6): Phase 3 is not considered complete until the intake pipeline has successfully ingested this repo's own content — `development/*.md`, `brain/raw/docs/*`, and any PDFs under `brain/raw/` — with `source` candidates generated and provenance preserved. The current wiki is thin (concepts=6, procedures=3, decisions=0, incidents=4, sources=0 as of 2026-04-20); self-hosting validates the pipeline and seeds the demo vault.

Deliverable:

- raw material intake spec
- true-raw archive spec
- projection plugin abstraction
- source candidate generation
- promotion queue integration
- self-hosted vault seeded from repo docs

### Phase 4: Promotion Policy Upgrade

Make promotion explicitly type-aware and explainable.

Actions:

- Add promotion policy per memory family.
- Promote raw material first into `source`, then candidate notes as evidence supports them.
- Require procedures to have goal, steps, applicability, failure modes, and success signal before active status.
- Require decisions to have rationale and validity metadata.
- Keep incomplete or low-confidence candidates in review or draft state.
- Add promotion explanations so MnemonicOS can say why something was promoted, updated, deferred, rejected, or routed to review.

Deliverable:

- type-aware promotion policy
- promotion explanation output
- tests for duplicate avoidance, deferral, and update-vs-new decisions

### Phase 5: Outcome-Aware Moat Hardening

Make retrieve -> outcome -> writeback -> maintenance the primary differentiator.

Actions:

- Ensure retrieved memories that precede success receive positive usefulness feedback.
- Ensure retrieved memories marked misleading are demoted, flagged, or queued for review.
- Generate incident or procedure-refinement candidates when repeated failures occur.
- Preserve stable `task_id` across retries so the feedback loop is attributable.
- Surface outcome feedback in maintenance summaries and evaluation metrics.

Demo claim:

- Most memory systems are static lookup tables or passive context layers. MnemonicOS improves based on what actually worked.

Deliverable:

- outcome feedback hardening plan
- eval slices for success, partial success, and misleading memory
- visible maintenance summary for outcome effects

### Phase 6: Governance And Permissions

Keep confidentiality and governance visible, but do not overclaim it as implemented.

Actions:

- Define bridge permissions:
  - scope
  - read
  - write raw
  - write canonical
  - promote
  - export
- Add audit logs for memory served, raw material archived, canonical notes updated, and promotion decisions.
- Preserve local-first and air-gap positioning as a market thesis and roadmap area.
- Align future access-control language with governance research where useful.

Deliverable:

- bridge permission model
- audit event schema
- governance roadmap section for board materials

## Acceptance Criteria

### Competitive Memo

- Every `partial` label has a specific reason: structural, terminology, scope, or operational.
- Every major competitor claim has a source link and date.
- Honcho and OpenClaw are treated as real promotion overlaps.
- The memo leads with outcome-aware runtime and universal raw intake, not generic slow promotion.
- The language uses "agent-independent, agent-facing" instead of "agent-free."

### Raw Intake

- Fixture PDF, HTML, DOCX, Markdown, and plain text files archive successfully.
- Each imported material creates a `source` candidate.
- Content-rich fixtures produce at least one typed candidate when evidence supports it.
- Raw archives remain immutable.
- Canonical notes contain provenance back to source material.

### Promotion

- Same material can produce different candidates by type without creating duplicate canonical notes.
- Weak or incomplete procedures stay draft or review.
- Contradictory decisions create review or supersession candidates instead of overwriting history.
- Promotion explanations are inspectable.

### Runtime Moat

- A retrieved memory that leads to success receives positive usefulness feedback.
- A retrieved memory marked misleading is demoted or flagged.
- Context packets expose recommendation and verification action, not only ranked hits.
- Repeated task failures produce useful incident or procedure-refinement candidates.

### Benchmark Story

Positioning claims need evidence, not only narrative. Acceptance:

- MnemonicOS runs on at least one public long-memory benchmark — candidates: **LongMemEval**, **LOCOMO**, or a MnemonicOS-specific agent-task eval (default recommendation: LongMemEval for comparability, plus a custom agent-task eval for outcome-loop validation).
- The benchmark reports outcome-aware retrieval vs. non-outcome retrieval scored on the same task set, so the outcome loop's contribution is isolable.
- Results are published in `evals/` with reproducible scripts.

## Assumptions

- The primary product bet is outcome-aware runtime plus universal raw intake.
- Governance remains important and visible, but secondary to the first two bets.
- MnemonicOS should avoid positioning itself as a database.
- MnemonicOS should avoid positioning itself as an agent framework.
- MnemonicOS should own the memory contract and lifecycle machinery while allowing external agents, databases, retrieval engines, and document processors to plug in.
- Current note kinds are good enough as the v1 durable folders; taxonomy v2 should first improve fields, validators, and promotion policy.

## Source Notes

Sources checked during planning:

- MnemonicOS competitive landscape PDF, dated 2026-04-17.
- Honcho Dreaming documentation, checked 2026-04-20.
- OpenClaw memory documentation, checked 2026-04-20.
- Mem0/OpenMemory product page and Mem0 paper summary, checked 2026-04-20.
- Zep/Graphiti overview, checked 2026-04-20.
- A-Mem paper page, checked 2026-04-20.
- MemOS paper page, checked 2026-04-20.
- Evolver README, checked 2026-04-20.

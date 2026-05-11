# LLM/VLM Layer Phase 5: LLM Candidate Bundle Layer

## Goal

Convert text windows plus visual snippets into structured decomposition bundles.
The LLM is not a summarizer. It should preserve the usable raw's information
shape by breaking it into source-grounded components that later deterministic
promotion can canonicalize into wiki notes, procedures, timelines, and graph
edges.

## Scope

The LLM layer consumes:

- `TextWindow` records
- source spans
- artifact markers
- VLM visual snippets
- projection metadata
- active source identity

It emits strict JSON candidate bundles. A candidate may contain a compact
`summary`, but the summary is an index hint, not the main payload. The important
payload is the decomposed source-local structure: events, claims, observations,
steps, failures, experiments, definitions, arguments, and relations. This layer
does not validate bundles as promotion-ready and does not write wiki.

## Candidate Bundle Contract

Schema version: `mnemos-extraction-v1`.

Supported item types:

- `source`
- `concept`
- `procedure`
- `decision`
- `incident`

Required item fields:

- `type`
- `title`
- `summary`
- `body`
- `confidence`
- `source_refs`
- `evidence`
- `components`

Optional item fields:

- `entities`
- `relations`
- `temporal_order`
- `importance`
- `freshness_hint`
- `relevance_hint`
- type-specific fields that map to current MnemonicOS note families

`components` is the loss-minimizing part of the payload. It is a list of
source-grounded units. Each component should include:

- `component_type`: one of `user_request`, `agent_action`, `failure`,
  `observation`, `web_finding`, `milestone`, `instruction`, `definition`,
  `claim`, `experiment`, `derivation`, `theorem`, `example`, `procedure_step`,
  or `open_question`;
- `text`: faithful decomposition of what the source says;
- `why_it_matters`: short importance/relevance note when available from the
  source context;
- `evidence`: source span, window id, source locator, quote, or artifact marker;
- `links`: typed local links to other components or candidate-level entities.

For agentic sessions, the LLM should preserve chronology and task boundaries.
One long session may produce multiple candidate groups, for example:

- user request;
- agent attempts;
- failures and error messages;
- searches or external artifacts consulted;
- findings imported from those artifacts;
- retries and fixes;
- milestones and final outcome;
- later user corrections or additional requests.

For textbook or document sources, the LLM should preserve document-local
structure, such as chapter/section/page/paragraph scope, key concepts, stated
experiments, deductions, derivative theorems, examples, and historical asides.
It must not invent missing theory; it should only decompose what the usable raw
supports.

## Authority Boundary

The LLM has authority to propose the source-local decomposition shape:

- how many candidates or nodes a source contains;
- how many traces, task threads, or document-local sections exist;
- where one trace starts and ends;
- how long a trace should be;
- which components belong in each trace;
- which source-grounded links likely connect components.

MnemonicOS software has final authority over admissibility and canonical memory:

- component evidence must bind to the active projection;
- component IDs, trace IDs, temporal order, and links must be internally valid;
- unsupported types or unsupported confidence/freshness/relevance vocabulary are
  rejected;
- candidate rows may become `promotion_ready`, but wiki writes remain the later
  deterministic promotion layer's responsibility.

## Defaults

- Local provider: Ollama.
- Default model: `gemma4:e4b`.
- Test provider: deterministic fixture provider with no external dependency.

## Milestones

1. Provider interface exists.
2. Ollama LLM provider exists behind the interface.
3. Fixture LLM provider exists for tests.
4. Candidate bundle JSON is stored with model metadata.
5. Per-item candidate rows are created.
6. Invalid model output is captured as an error state.

## Verification

- Fixture provider produces valid source and concept candidates with
  source-grounded components.
- Visual snippet content is included in the LLM input packet.
- Invalid JSON becomes an error and does not create promotion-ready candidates.
- Unsupported top-level schema version is rejected.
- Candidate rows preserve source refs, evidence, components, relations, and
  chronology hints.
- No LLM path writes wiki.

## Out Of Scope

- Deterministic promotion-ready validation.
- Duplicate/update/new policy.
- Wiki rendering or mutation.

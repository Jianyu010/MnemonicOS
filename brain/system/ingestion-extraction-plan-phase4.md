# LLM/VLM Layer Phase 4: VLM Visual Snippet Layer

## Goal

Specialize visual processing before text candidate extraction.

## Scope

The VLM layer consumes `ArtifactView` objects and emits visual snippets keyed by
west-flange artifact identity. It does not mutate west-flange metadata.

## Provider Contract

A VLM provider accepts:

- artifact id
- kind
- marker
- source locator
- MIME type
- payload bytes when available
- source attachment path when available
- context window ids
- warnings

It returns:

- artifact id
- marker
- source locator
- visual description
- visible text if any
- uncertainty/warnings
- model metadata

## Defaults

- Local provider: Ollama.
- Default model: `llama3.2-vision:latest`.
- Test provider: deterministic fixture provider with no external dependency.

## Milestones

1. Provider interface exists.
2. Ollama VLM provider exists behind the interface.
3. Fixture VLM provider exists for tests.
4. Visual snippets persist in extraction-layer tables.
5. Missing payload/path produces a warning and no crash.
6. West-flange metadata remains free of VLM descriptions.

## Verification

- Fixture provider emits stable snippet for an artifact payload.
- Fixture provider emits stable snippet for a source attachment.
- Missing payload and missing source attachment path are handled safely.
- Snippet row preserves `artifact_id`, marker, source locator, and model name.
- Search/snapshot check confirms no VLM text is persisted in west-flange
  archive metadata.
- No VLM test writes wiki.

## Out Of Scope

- LLM candidate extraction.
- Candidate validation.
- OCR as a west-flange concern.


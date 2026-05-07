# LLM/VLM Layer Phase 6: Deterministic Candidate Validation

## Goal

Terminate the LLM/VLM layer at validated, promotion-ready candidate records.

## Scope

Validation is deterministic software logic. It evaluates model output but does
not perform wiki writes.

## Validation Gates

A candidate can become `promotion_ready` only when all gates pass:

- bundle schema version is supported;
- candidate type is supported;
- required fields are non-empty;
- confidence is one of the supported values;
- `source_refs` includes the active source;
- evidence exists;
- evidence points to a source span, source locator, or artifact marker;
- components exist for non-trivial sources and each component has evidence;
- component links only target declared components, candidate entities, or
  source-grounded refs;
- candidate can normalize into current MnemonicOS note families;
- candidate is serializable as deterministic promotion input.

## Candidate States

- `invalid`: failed validation, with errors.
- `valid`: structurally valid, but not ready for deterministic promotion.
- `promotion_ready`: deterministic promotion layer may consume it later.

## Milestones

1. Validation module exists.
2. Validation errors are structured and persisted.
3. Source-ref and evidence checks work for artifacts.
4. Source-ref and evidence checks work for episodes.
5. Normalization checks cover all supported candidate types.
6. No validation path writes wiki.

## Verification

- Valid candidate becomes `promotion_ready`.
- Candidate with wrong source ref is invalid.
- Candidate with no evidence is invalid.
- Candidate with components lacking evidence is invalid.
- Candidate with dangling component links is invalid.
- Candidate with unsupported note type is invalid.
- Candidate with malformed type-specific fields is invalid.
- Candidate normalization failure is invalid.
- Validation produces stable error messages for tests.
- Test asserts `brain/wiki/` remains unchanged.

## Out Of Scope

- Actual wiki rendering.
- Duplicate/update/new write decisions.
- Review item creation.

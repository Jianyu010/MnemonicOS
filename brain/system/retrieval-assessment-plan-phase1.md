# Retrieval Assessment Phase 1: AssessedPacket and AssessmentDirective Contract

## Goal

Define the Stage 6 packet contract before changing runtime behavior. The
contract must preserve the Stage 5 packet unchanged while adding a deterministic
assessment layer that Stage 7 can follow without reinterpreting memory.

## Scope

- `AssessedPacket` type
- `AssessmentDirective` type
- enum values for state, completion, reuse, emission, hydration, and follow-up
- serialization shape for runtime/API/shell callers
- compatibility with existing `retrieval_state`, `recommendation`, and
  `verification_action` fields

## Components

```text
src/second_brain/models.py       — eventual dataclasses / enums
src/second_brain/runtime.py      — temporary integration point
src/second_brain/assessor.py     — eventual Stage 6 module
tests/test_runtime_protocol.py   — contract fixtures
api/openapi.yaml                 — later public schema exposure if needed
```

## Contract Shape

```text
AssessedPacket:
  packet: MemoryPacket
  directive: AssessmentDirective

AssessmentDirective:
  retrieval_state: RetrievalState
  completion_state: CompletionState
  reuse_mode: ReuseMode
  recommendation: Recommendation
  verification_action: VerificationAction
  emission_profile: EmissionProfile
  hydration_policy: HydrationPolicy
  selected_branch_ids: list[str]
  deferred_branch_ids: list[str]
  missing_roles: list[str]
  followup_query_policy: FollowupQueryPolicy
  fresh_pipeline_threshold: FreshPipelineThreshold
  assessment_reasons: list[str]
```

## Contract Rules

1. Stage 6 wraps the Stage 5 packet; it must not mutate canonical packet slots.
2. Existing top-level runtime fields remain available for compatibility:
   `retrieval_state`, `recommendation`, and `verification_action`.
3. New directive fields are machine-readable and use closed vocabularies.
4. `assessment_reasons` are short reason codes or compact strings, not prose
   essays.
5. Empty/no-hit packets still produce a valid directive.
6. Errors degrade to a minimal directive rather than blocking emission.

## Enum Vocabulary

```text
RetrievalState:
  sufficient_current
  sufficient_historical
  partial
  insufficient_no_hit
  insufficient_stale
  insufficient_contested

CompletionState:
  complete_path
  success_only
  partial_chain
  fragmentary
  overabundant

ReuseMode:
  direct_reuse
  reuse_with_verification
  scaffold_only
  diagnose_on_failure
  fresh_research_required

EmissionProfile:
  concise_success_path
  success_plus_major_failures
  full_chain
  warning_only
  no_memory
```

## Verification

- serialized `AssessedPacket` includes the original packet and directive
- compatibility fields match directive values
- no-hit, archive-only, contested, and overabundant fixtures serialize cleanly
- unknown internal errors degrade to a minimal `fresh_research_required`
  directive

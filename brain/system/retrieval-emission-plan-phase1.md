# Retrieval Emission Phase 1: Emission Contract and Output Profiles

## Goal

Define the Stage 7 render contract before touching implementation. The output
of this phase is a stable `EmissionEnvelope` / render-model design that both
JSON and Markdown renderers can consume.

## Scope

- canonical render model
- compatibility fields
- profile mapping from `AssessmentDirective`
- selected/deferred packet view
- focused requery handoff model
- automatic retry metadata contract
- caller-facing debug/provenance policy

## Components

```text
src/second_brain/emission.py      — future render model and profile mapping
src/second_brain/models.py        — future typed envelope if needed
tests/test_emission.py            — contract fixtures
```

## Contract Sketch

```text
EmissionEnvelope:
  protocol
  version
  operation
  retrieval_state
  completion_state
  reuse_mode
  recommendation
  verification_action
  emission_profile
  selected_sections
  archive_context
  warnings
  contradictions
  missing_roles
  deferred_context
  followup_query_policy
  followup_query
  followup_query_anchors
  retry_control
  provenance
  debug
```

`EmissionEnvelope` is a presentation view over the packet, not a new retrieval
result. It should contain only fields derived from `MemoryPacket`,
`AssessmentDirective`, and existing runtime metadata.

## Profile Mapping

| Directive profile | Envelope behavior |
|-------------------|-------------------|
| `concise_success_path` | selected primary/path items only, brief warning summary |
| `success_plus_major_failures` | selected success path plus selected failure/caution branches |
| `full_chain` | selected functional path/reservoir branches in explanatory order |
| `warning_only` | no primary readout; warnings, contradictions, missing roles, action |
| `no_memory` | explicit empty-memory payload and fresh-research action |

## Decisions

- JSON is canonical for schema stability.
- Markdown is derived from the same envelope.
- `AssessmentDirective` wins over duplicated compatibility fields.
- `followup_query` is an emitted instruction for the caller, not an internal
  pipeline action.
- `followup_query_anchors` should carry stable identifiers when available:
  original intent, missing roles, note IDs, chain ID, branch ID, error
  signature, and relation target ID.
- `retry_control` describes whether an external orchestrator may retry, how
  many attempts are allowed, and why retries should stop.
- `debug` remains structured and may be hidden by caller policy later, but the
  first design should preserve current debug visibility for parity.

## Verification

- fixture for every `emission_profile`
- fixture where directive and compatibility fields disagree
- fixture for `immediate_focused_requery` with a suggested query
- fixture for suggested query anchors containing note IDs and chain ID
- fixture for `on_first_failure` with a conditional query sketch
- fixture for automatic retry disabled by default
- fixture for retry cutoff after no new IDs
- fixture for empty packet
- fixture for archive-only packet
- fixture proving envelope construction does not mutate the packet

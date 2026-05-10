# LLM/VLM Layer Phase 3: Projection Consumer

## Goal

Consume west-flange RAM projection output without raw-source-specific logic.

## Scope

The worker opens a `ProjectionLease` from a queued `SourceRef`, pulls text and
artifact lanes, records work metadata, and exits without model calls in dry-run
mode.

## Flow

1. Load pending `extraction_queue` row.
2. Convert it to `SourceRef`.
3. Call `ProjectionService.open_projection(source_ref)`.
4. Inspect `ProjectionLease.duplicate_state`.
5. Pull `iter_text_windows()`.
6. Pull `iter_artifacts()`.
7. Record counts and warnings on the queue row or job result.
8. Always close the lease.

## Duplicate Handling

- `exact_duplicate`: skip when prior candidate output can be reused; otherwise
  record a skipped/error state according to available prior state.
- `canonical_duplicate`: project fully.
- `possible_duplicate`: project fully.
- `overlap_related`: project fully and preserve source identity for later
  candidate provenance.
- `unique`: normal projection.

## Milestones

1. Worker can dry-run artifact projection.
2. Worker can dry-run episode projection.
3. Exact duplicate redirect is handled without connector parsing.
4. Non-exact duplicate sources produce text windows.
5. Artifact payload caps produce warnings, not crashes.
6. Lease cleanup happens on success and failure.

## Verification

- Dry-run worker opens projection and reports window/artifact counts.
- Exact duplicate source returns thin redirect behavior.
- Canonical duplicate still yields text windows.
- Source attachment artifact appears in artifact lane.
- Payload-capped artifact yields metadata and warning.
- Projection error moves queue row to error.
- No dry-run or projection-consumer path writes wiki.

## Out Of Scope

- VLM visual description.
- LLM candidate extraction.
- Candidate schema validation.


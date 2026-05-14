# Retrieval Assessment Phase 2: Trustworthiness Classifier

## Goal

Classify whether the assembled memory is safe enough to use. This phase owns
the compatibility `retrieval_state` decision and the recommendation/verification
policy derived from trust, freshness, quality, authority, and contradiction
signals.

## Scope

- `retrieval_state`
- `recommendation`
- `verification_action`
- all-primary quality gate
- contradiction and supersession handling
- archive-only distinction from no-hit
- historical reuse behavior

## Components

```text
src/second_brain/assessor.py     — eventual trust classifier
src/second_brain/runtime.py      — current compatibility functions to migrate
src/second_brain/models.py       — enum values / directive fields
tests/test_runtime_protocol.py   — trust-state fixtures
```

## Inputs

- `MemoryPacket.primary_memories`
- `MemoryPacket.archive_context`
- `MemoryPacket.contradictions`
- `MemoryPacket.warnings`
- `MemoryPacket.verification_needed`
- `assembly_notes.archive_only`
- `assembly_notes.warning_item_ids`
- query temporality
- parsed query anchors / type, when available

## State Rules

Precedence is most severe first:

| State | Condition |
|-------|-----------|
| `insufficient_contested` | Contradictions block safe primary branch selection. |
| `insufficient_stale` | All primary memories are stale for a current query. |
| `insufficient_no_hit` | No primary memories and no archive context. |
| `sufficient_historical` | Historical query with at least one relevant primary memory. |
| `partial` | Any primary memory is suspect, quality-failed, archive-only-actionable, or carries unresolved verification warnings. |
| `sufficient_current` | All primary memories are fresh/aging, quality-passed, non-contested, and relevant. |

Archive-only packets are not no-hit packets. They usually classify as `partial`
or `insufficient_stale` depending on caller need and authority requirements.

## Recommendation Rules

```text
sufficient_current + complete enough -> reuse
sufficient_historical -> reuse_with_verification
partial -> reuse_with_verification or scaffold_only
insufficient_* -> research_fresh
```

Historical memory must not return direct `reuse`. Contradiction-blocked memory
must not return direct `reuse`.

## Quality Gate

The quality gate applies to every primary memory. A single empty, stubby,
suspect, or quality-failed primary can downgrade the packet to `partial` even if
the top hit looks good.

## Verification

- all-primary quality gate downgrades mixed-quality packets
- archive-only packet is distinguishable from no-hit packet
- historical packet recommends `reuse_with_verification`
- contested primary produces `insufficient_contested`
- stale current packet produces `insufficient_stale`
- sufficient current packet preserves existing compatibility behavior

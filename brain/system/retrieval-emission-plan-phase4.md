# Retrieval Emission Phase 4: Archive, Warning, and Contradiction Presentation

## Goal

Make caution-bearing output impossible to miss. Archive chunks, warnings,
contradictions, stale/suspect state, and missing roles must be visible in both
JSON and Markdown without being mixed into canonical primary memory.

## Scope

- `archive_context` rendering
- warning rendering
- contradiction rendering
- stale/suspect/contested flags
- verification and fresh-research action text

## Presentation Rules

- Archive evidence appears only under `archive_context` / `Archive Evidence`.
- Archive evidence is labeled raw, session-derived, or non-canonical using
  provenance already carried by the packet.
- Contradictions are shown as structured pairs or concise bullet warnings.
- `verification_action` is rendered near the top and near the warning section
  when warnings are present.
- `missing_roles` must appear when completion is not `complete_path`.

## Severity Guidance

| Condition | Rendering |
|-----------|-----------|
| `insufficient_contested` | warning-only unless selected directive says otherwise |
| `insufficient_stale` | warning-only or no-memory with fresh-research action |
| `partial` | selected path plus verification action |
| archive-only actionable | archive evidence plus non-canonical label |
| missing required role | missing role list plus follow-up/fresh threshold |

## Verification

- contested packet does not render as direct reuse
- stale packet does not hide `verify_current_truth` / fresh-research action
- archive context never appears in primary memory section
- missing roles visible in JSON and Markdown
- contradiction pairs preserve source IDs

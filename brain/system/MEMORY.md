# MEMORY
updated_at: 2026-04-22

## Repository Conventions
- Fill in local coding and documentation conventions here.

## Agent Behavior
- Save raw session files before any canonical write.
- Default to archive unless promotion rules clearly apply.
- Write review items instead of forcing ambiguous merges.
- Set `summary:` field as a gene string: `WHEN [trigger] → DO [action] (NOT [anti-pattern])`. This is the primary signal agents read on retrieval.
- Set `resolution_state: open|verified|failed` on all incident notes.
- Set `recovery_path:` on all incident notes (even if empty string for unresolved).

## Promotion Criteria for Procedures

A procedure note is eligible for promotion from `wiki/procedures/drafts/` to `wiki/procedures/active/` when ALL of:

1. `source_refs` count >= 5 (observed across multiple sessions)
2. `summary` field is a gene string — not empty, not a copy of title
3. At least 1 confirmed useful retrieval in `task_outcomes` table (outcome_type is not `misleading`)

Convergence alone is NOT sufficient — many `run_python exit-1` entries are noise convergence (same buggy pattern repeated), not procedure quality.

Promotion steps:
1. Move file from `wiki/procedures/drafts/` to `wiki/procedures/active/`
2. Update `lifecycle_state: canonical` in frontmatter
3. Run sync (daemon or `mnemonicos sync`) to re-index in DB
4. Verify: run `scripts/mnemonic_context.sh "<relevant query>"` and confirm note appears in results

## Content Quality Standard

A note passes content quality if ANY of:
- `summary` is non-empty and differs from `title` (gene string present)
- Body excerpt contains 20+ words
- Body excerpt contains actionable markers: WHEN / DO / Steps / Cause / Fix / Avoid / →

Notes that fail content quality are downgraded from `sufficient_current` to `partial` at retrieval time (see `runtime.py:_hit_has_content_quality`).

## Environment
- Fill in machine, language, and toolchain details here.

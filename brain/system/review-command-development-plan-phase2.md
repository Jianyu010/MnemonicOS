# Review Command — Phase 2: Basic CLI

## Goal

Wire Phase 1 functions into `mnemonic review` subcommands for non-interactive
(scriptable) use: listing, showing, approving, rejecting.

## Context

Humans and automation scripts need a stable, pipe-friendly interface. The interactive
TUI (Phase 3) is built on top of this; Phase 2 remains as fallback.

## Scope

**Modified file:** `src/second_brain/cli.py`

### Subcommands added

```
mnemonic review list   [--json]
mnemonic review show   <name>
mnemonic review approve <name> [--lifecycle-state draft|verified|canonical|contested|archived] [--dry-run]
mnemonic review reject  <name> [--dry-run]
```

### Parser wiring

`review` parser with `review_command` sub-subparsers, `required=True`.
All four sub-subcommands registered with appropriate arguments.

### Handler

`_review(config_path, args)` dispatches to `review_ops` functions.
`list` with no `--json` prints numbered table: `idx  reason  type  title`.
`approve` prints `approved=<final_path>` on success or `error: <FileExistsError>` on collision.
`reject` prints `rejected`.
`show` prints raw file content.

## Milestones

1. Parser wiring + dispatch in `main()`
2. `_review` handler function
3. Manual smoke test against live `brain/wiki/review/`

## Verification

```bash
mnemonic review list
mnemonic review approve <name> --dry-run
mnemonic review approve <name>
mnemonic review reject  <name>
```

`python -m pytest tests/ -q` → 256 passed (no regression).

## Out of Scope

Interactive TUI, HTTP surface, `--force` merge flag.

## Status

**Complete** (implemented 2026-05-08). 256/256 tests pass.

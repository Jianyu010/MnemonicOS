# Retrieval Recall Phase 5: Archive Channel and Policy Gate

## Goal

Implement the archive/raw lane as a separate recall path governed by explicit
policy, rather than as ordinary canonical-memory recall.

## Scope

- `decide_archive_policy()` function
- `run_archive_channel()` function
- raw-intent detection for admissibility
- archive candidate contract

## Components

```
src/second_brain/recall.py
src/second_brain/query_parser.py
```

## Policy Modes

`ArchivePolicyDecision.mode` is one of:

- `disabled`
- `explicit_raw_intent`
- `fallback_only`

### Explicit raw intent indicators

Examples:

- raw
- original
- transcript
- quote
- exact wording
- provenance
- source material

## Rules

1. Normal canonical recall must not implicitly become archive recall.
2. Archive recall is allowed when:
   - the query expresses explicit raw intent, or
   - caller/runtime policy enables fallback-only behavior
3. Archive candidates stay in the archive lane only.
4. Archive candidate count is capped at `top_k`.

## Failure Policy

If archive policy is disabled, return `[]` without error.

## Verification

- normal knowledge queries do not produce archive candidates
- explicit raw-intent queries may produce archive candidates
- archive candidates are typed separately from wiki candidates
- archive result count respects `top_k`

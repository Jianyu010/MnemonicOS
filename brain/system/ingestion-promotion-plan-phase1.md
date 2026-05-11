# Promotion Layer Phase 1: Promotion Gate

## Goal

Verify every `promotion_ready` candidate against seven deterministic checks
before allowing any wiki write.

## Context

The extraction layer's `validation.py` runs structural checks when a candidate
is first produced by the LLM. Candidates can sit in `extraction_candidates` for
hours or days before the promotion daemon picks them up. The promotion gate is a
second, independent pass: it re-verifies structural integrity against the stored
payload and re-checks evidence binding, types, limits, and config policy.

This is defense-in-depth, not redundancy. The gate catches state that was valid
at extraction time but has since degraded, and it enforces promotion-layer limits
(component count, policy flag) that are out of scope for the extraction validator.

There is intentionally no cap on the number of candidates a single source may
produce. A complex source (a graduate textbook, a multi-session research thread)
may legitimately yield hundreds of candidates. Quality control operates at the
per-candidate level — confidence, evidence binding, component completeness — not
at the per-source count level. Volume is not a quality signal in either direction.

## Scope

New module: `src/second_brain/promotion/gate.py`

New dataclass `GateResult` in `src/second_brain/promotion/models.py`:
- `ok: bool`
- `errors: list[str]`

The gate imports private helpers from `src/second_brain/extraction/validation.py`
directly. It does not copy or reimplement them.

## The Seven Checks

Checks run in order. All failures are collected before returning — the gate does
not short-circuit on the first error.

### Check 1 — Every component has source evidence

Re-run `_validate_components(payload["components"], evidence_ids, errors)` from
`validation.py`. Every component must include an `evidence` list that contains at
least one entry matching a known evidence ID from the candidate's projection.

Error code: `component_evidence_not_bound_to_projection:{component_id}`

### Check 2 — Every trace and component ID is valid

Re-run `_validate_components` and `_validate_traces` from `validation.py`.
Component IDs must be unique and non-empty. Trace member IDs must reference
declared component IDs.

Error codes: `component_duplicate_id:{id}`, `component_missing_id:{index}`,
`trace_component_dangling:{trace_id}->{component_id}`

### Check 3 — Links point to real components

Re-run `_validate_component_links(components, component_ids, errors)` from
`validation.py`. Every `links[].target` value must reference a declared component
ID within the same candidate.

Error code: `component_link_dangling:{component_id}->{target}`

### Check 4 — Trace boundaries are source-grounded

Re-run `_validate_traces(traces, component_ids, errors)` from `validation.py`.
Every trace must have a non-empty `component_ids` list whose members are all
declared component IDs.

Error code: `trace_component_dangling:{trace_id}->{component_id}`

### Check 5 — No unsupported type appears

Verify `candidate.candidate_type` against `SUPPORTED_CANDIDATE_TYPES` from
`extraction/models.py`. Verify every component's `component_type` against
`SUPPORTED_COMPONENT_TYPES` from the same module.

Error codes: `unsupported_candidate_type:{type}`,
`unsupported_component_type:{type}`

### Check 6 — Component count within configured limit

Count the components in `payload["components"]`. Compare against
`config.promotion.max_components_per_candidate`.

Error code: `component_count_exceeds_limit:{count}>{limit}`

### Check 7 — Promotion policy is enabled

Check `config.promotion.enabled`. If false, gate blocks all candidates
regardless of their content.

Error code: `promotion_policy_disabled`

## Evidence ID Reconstruction

The promotion gate reconstructs the evidence ID set from the stored
`payload_json`. It calls `projection_evidence_ids(packet)` from `validation.py`
where `packet` is the candidate payload itself (the `evidence` list and
component evidence references are sufficient for re-checking binding; the
original projection packet is not required at promotion time).

## Gate Interface

```python
def run_gate(
    payload: dict,
    *,
    config: AppConfig,
) -> GateResult:
    ...
```

Takes the deserialized candidate payload and the app config. Returns a
`GateResult`. Does not write to the database.

## Milestones

1. `gate.py` module exists with `run_gate()` function.
2. `GateResult` dataclass exists in `promotion/models.py`.
3. Each of the 7 checks is exercised by at least one test.
4. A candidate with all checks passing returns `GateResult(ok=True, errors=[])`.
5. Each check independently returns the correct error code on failure.
6. Gate does not write to wiki or the database.

## Verification

- Clean `promotion_ready` fixture candidate passes all 7 checks.
- Candidate with component missing evidence fails check 1.
- Candidate with dangling trace member fails checks 2 and 4.
- Candidate with dangling component link fails check 3.
- Candidate with unsupported candidate type fails check 5.
- Candidate with unsupported component type fails check 5.
- Candidate with 51 components (limit=50) fails check 6.
- Gate with `config.promotion.enabled = false` fails check 7 for any candidate.
- `brain/wiki/` is unchanged after running the gate.

## Out Of Scope

- Wiki writes.
- The `new` / `confirm` / `update` / `review` decision (Phase 2).
- Note rendering (Phase 3).
- Database ledger writes (Phase 4).

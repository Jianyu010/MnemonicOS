# Retrieval Signal Integration Phase 1: Signal Contract and Defaults

## Goal

Define the typed Stage 3 output contract and deterministic defaults for every
signal consumed by Stage 4.

## Scope

- `NoteSignals`
- `SignalEnrichedCandidate`
- `SignalEnrichedArchiveCandidate`
- `SignalEnrichedCandidatePool`
- defaulting rules for missing maintenance rows
- passive feedback state/confidence fields backed by `note_passive_feedback`

## Components

```text
src/second_brain/signal_integration.py  — new Stage 3 module
src/second_brain/models.py              — shared dataclasses if preferred
src/second_brain/retrieval.py           — migrate inline enrichment out
```

## Contract Sketch

```python
@dataclass(slots=True)
class NoteSignals:
    trust_score: float
    freshness_score: float
    freshness_state: str
    importance_score: float
    type_prior_score: float
    summary_quality: str
    quality_pass: bool
    quality_reasons: list[str]
    freshness_multiplier: float
    passive_feedback_state: str
    passive_feedback_confidence: float
```

`passive_feedback_state` values:
- `none`
- `passive_used_likely`
- `passive_useful_likely`
- `passive_misleading_likely`

`passive_feedback_confidence` is copied from the selected
`note_passive_feedback.confidence` row once Maintenance migration 026 is
available.

## Default Rules

1. Missing trust row: `trust_score = 0.0`.
2. Missing freshness row: derive date-based freshness score and state `fresh`.
3. Missing importance row: `importance_score = 0.0`.
4. Missing query type: `type_prior_score = 0.0`.
5. Missing summary/control signal: `summary_quality = "none"` and
   `quality_pass = False` unless another compact signal is present.
6. Missing passive evidence: `passive_feedback_state = "none"` and confidence `0.0`.

## Verification

- absent maintenance rows produce deterministic defaults
- no signal field is nullable unless explicitly modeled as optional
- Stage 4 test fixture can score the output without DB access
- missing `note_passive_feedback` table or rows degrade to `none` / `0.0`

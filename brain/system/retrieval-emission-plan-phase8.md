# Retrieval Emission Phase 8: 2D Score Vector and Relevance-Suppression Signals

## Goal

Surface the new two-layer composite score model to callers. Each emitted hit
gains `relevance_score` and `quality_score` fields alongside `final_score`
(which equals `composite_score`). Gate suppression counts and suppression rate
appear in the assessment block. `insufficient_no_hit` due to gate suppression
emits a clear human-readable message distinguishing it from a corpus miss.

## Scope

- JSON: each hit object gains `relevance_score` and `quality_score` fields
- Markdown: each hit line gains a compact confidence tag based on composite_score
- Assessment block (JSON + Markdown): `suppressed_by_gate_count`,
  `passed_gate_count`, `suppression_rate`
- Human-readable gate-suppressed message for `insufficient_no_hit` with reason
  `relevance_gate_suppressed_all`
- No breaking change to existing JSON fields; new fields are additive
- Direct retrieve inspection body hydration remains additive and independent of
  the 2D score fields

## Components

```text
src/second_brain/emission.py     — render 2D fields, gate message, suppression metadata
src/second_brain/cli.py          — pass relevance/quality in direct retrieve text output; optional body inspection
scripts/mnemonic_context.sh      — Markdown rendering of confidence tags; optional JSON body pass-through
tests/test_emission.py           — 2D field presence, gate-suppressed message fixture
```

## JSON Schema Changes

Each hit object in `hits[]`:

```json
{
  "id": "...",
  "title": "...",
  "type": "...",
  "final_score": 0.312,
  "relevance_score": 0.41,
  "quality_score": 0.76,
  ...
}
```

Assessment block in the JSON response:

```json
{
  "retrieval_state": "insufficient_no_hit",
  "recommendation": "research_fresh",
  "assessment_metadata": {
    "suppressed_by_gate_count": 5,
    "passed_gate_count": 0,
    "suppression_rate": 1.0,
    "suppression_reason": "relevance_gate_suppressed_all"
  }
}
```

## Markdown Rendering

### Hit Line Format

Each hit in the Markdown context block gains a bracketed confidence band after
the score:

```text
1. concept/hook-firing-mechanism [concept] channels=bm25,semantic score=0.312
   relevance=0.41 quality=0.76
   title: Hook Firing Mechanism
   ...
```

The `relevance=` and `quality=` line is compact (two values on one line) to keep
context block size minimal.

### Confidence Band (optional, for human-facing CLI output only)

For the direct `mnemonicos retrieve` CLI (not the agent hook output), annotate
each hit with a one-word confidence label:

| composite_score range | Label |
|----------------------|-------|
| ≥ 0.50 | HIGH |
| 0.30 – 0.49 | MEDIUM |
| < 0.30 | LOW |

```text
1. [HIGH] concept/hook-firing... score=0.62 relevance=0.81 quality=0.76
2. [MEDIUM] procedure/promotion-gate... score=0.34 relevance=0.45 quality=0.76
3. [HIGH] session/2026-05-04-... [archive_chunk] channels=archive score=1.000 [rank-based]
```

This label is **not** emitted in the agent hook Markdown block (adds token cost
with little LLM benefit). It is for human diagnostics only.

### Gate-Suppressed Message

When `retrieval_state = insufficient_no_hit` and reason =
`relevance_gate_suppressed_all`, the Markdown context block emits:

```markdown
### Relevant Notes

- No notes met the relevance threshold. 5 candidates were recalled but
  suppressed (relevance_score < 0.25). Query likely outside MnemonicOS corpus.
  Proceed with own reasoning; do not treat prior hits as authoritative.
```

For the legacy `no_primary_or_archive` reason (true corpus miss):

```markdown
### Relevant Notes

- No relevant MnemonicOS notes found.
```

This distinction lets agents and humans immediately understand whether the wiki
was empty or the query was out-of-scope.

### High Suppression Rate Warning

When `suppression_rate > 0.50` and at least some hits passed the gate, append a
one-line advisory to the context block:

```markdown
<!-- retrieval note: >50% of candidates were gate-suppressed; consider
     broadening query terms if context seems thin -->
```

This line is an HTML comment — invisible in rendered Markdown but visible to
agents reading raw text.

## Rules

1. `relevance_score` and `quality_score` are emitted for every **wiki** hit.
   **Archive hits** (`type="archive_chunk"`) are scored via a separate rank-based
   formula (`1.0 - ((rank - 1) / top_k)`) and carry no meaningful 2D sub-scores;
   `relevance_score` and `quality_score` remain at their default `0.0` and must
   not be displayed. Display shows `score=X.XXX [rank-based]` instead.
2. `final_score` continues to equal `composite_score`. No renaming — downstream
   consumers reading `final_score` are unaffected.
3. Confidence band labels appear in CLI text output only, not in agent hook
   Markdown or JSON API responses.
4. Suppression metadata appears in all output modes (JSON, Markdown, CLI text)
   when `suppressed_by_gate_count > 0`.
5. Gate-suppressed message must not suggest the query could be refined to get
   hits from MnemonicOS — the correct action is fresh research, not requery.
6. `retrieval_state` in the output header must still be the first line of the
   assessment block so agents reading the context block see it immediately.
7. `--include-body` / `include_body` does not alter scores, relevance gate
   behavior, hit count, or ranking order. It reads selected wiki `body_path`
   files only after retrieval has completed.

## Verification

- JSON fixture: hit objects contain `relevance_score` and `quality_score`
- Gate-suppressed fixture: Markdown contains suppressed count and advisory message
- High suppression rate fixture: HTML comment advisory present when rate > 0.50
- CLI text output: confidence band labels present
- Agent hook Markdown output: no confidence band labels
- `final_score` unchanged for all existing fixtures (no regression)
- Direct retrieve inspection fixture: body fields are absent by default and
  present only under explicit hydration.

---

## Reference

**Deep research mode:** see "Deep Research Mode (Future)" in [retrieval-emission-plan.md](retrieval-emission-plan.md) — when implemented, that mode supersedes the `max_hits` and token caps described in Phase 4 and Phase 8; 2D scores and confidence labels still apply.

**Governing plan:** [retrieval-emission-plan.md](retrieval-emission-plan.md)
**Prior phases:** [P1](retrieval-emission-plan-phase1.md) · [P2](retrieval-emission-plan-phase2.md) · [P3](retrieval-emission-plan-phase3.md) · [P4](retrieval-emission-plan-phase4.md) · [P5](retrieval-emission-plan-phase5.md) · [P6](retrieval-emission-plan-phase6.md) · [P7](retrieval-emission-plan-phase7.md)
**Score model:** [retrieval-reranking-plan-phase6.md](retrieval-reranking-plan-phase6.md)
**Assessment:** [retrieval-assessment-plan-phase8.md](retrieval-assessment-plan-phase8.md)

# Knowledge Maintenance Phase 4: Importance Scoring — Unified Signal

## Goal

Compute a unified per-note importance score daily, combining graph connectivity,
retrieval trust, and freshness into a single value stored in a dedicated table.
Expose this score in retrieval hit features for use by the rerank model.

## Context

Two partial signals already exist:
- `note_trust.usefulness_score`: retrieval utility derived from top-1 correctness
  and useful/not-useful feedback.
- `note_freshness.staleness_score`: recency and contradiction-weighted staleness.

What is missing is a signal that incorporates the note's structural position in
the knowledge graph — how many notes reference it, how many it references — and
combines all signals into a single priority score. This matters for two consumers:

1. **Retrieval ranking**: the rerank model currently has `trust_score` and
   `freshness_score` as separate features. An `importance_score` feature gives it
   a pre-combined signal that reflects graph centrality.
2. **Maintenance prioritization**: the weekly hygiene job can use importance scores
   to decide which notes to surface first in health reports and which stale notes
   to prioritize for relearn tasks.

Phase 4 adds the DB table, the computation function, the daily wiring, and the
retrieval integration.

## Scope

### Migration: `migrations/019_note_importance.sql`

```sql
CREATE TABLE IF NOT EXISTS note_importance (
    note_id TEXT PRIMARY KEY,
    importance_score REAL NOT NULL DEFAULT 0.0,
    in_degree INT NOT NULL DEFAULT 0,
    out_degree INT NOT NULL DEFAULT 0,
    connectivity_score REAL NOT NULL DEFAULT 0.0,
    trust_score REAL NOT NULL DEFAULT 0.0,
    freshness_score REAL NOT NULL DEFAULT 0.0,
    lifecycle_weight REAL NOT NULL DEFAULT 0.5,
    updated_at TEXT
);
```

### `ImportanceSignal` dataclass in `maintenance.py`

```python
@dataclass(slots=True)
class ImportanceSignal:
    importance_score: float
    in_degree: int
    out_degree: int
    connectivity_score: float
    trust_score: float
    freshness_score: float
    lifecycle_weight: float
```

### `compute_importance_scores(connection) -> dict[str, ImportanceSignal]`

Algorithm:

**Step 1 — Graph degree counts:**

```sql
SELECT
    n.id,
    COUNT(DISTINCT out_e.target_id) AS out_degree,
    COUNT(DISTINCT in_e.source_note_id) AS in_degree
FROM notes n
LEFT JOIN graph_edges out_e ON out_e.source_note_id = n.id
LEFT JOIN graph_edges in_e ON in_e.target_id = n.id
GROUP BY n.id
```

**Step 2 — Load existing trust and freshness:**

Use `load_note_trust(connection, note_ids)` and
`load_note_freshness(connection, note_ids)` from `trust.py` (already exist).

These must have already been computed by `compute_note_trust_stats` and
`compute_note_freshness` in the same job run. Phase 4 runs after them.

**Step 3 — Compute scores per note:**

```python
# connectivity_score: weighted in-degree dominant, sigmoid-normalized
raw_connectivity = in_degree + 0.4 * out_degree
connectivity_score = 2.0 / (1.0 + math.exp(-raw_connectivity / 5.0)) - 1.0
# maps 0 → 0.0, 5 → ~0.73, 10 → ~0.90

# trust_score: from note_trust.usefulness_score (0.0–1.0), default 0.5 if absent
trust_score = trust.usefulness_score if trust else 0.5

# freshness_score: 1 - staleness_score (fresher = higher)
freshness_score = 1.0 - freshness.staleness_score if freshness else 0.5

# lifecycle_weight
LIFECYCLE_WEIGHTS = {
    "canonical": 1.0,
    "verified": 0.70,
    "draft": 0.40,
    "review": 0.30,
    "contested": 0.20,
    "archived": 0.05,
}
lifecycle_weight = LIFECYCLE_WEIGHTS.get(lifecycle_state or "draft", 0.40)

# combined importance
importance_score = (
    0.35 * connectivity_score
    + 0.25 * trust_score
    + 0.25 * freshness_score
    + 0.15 * lifecycle_weight
)
```

**Step 4 — Upsert to `note_importance`:**

```sql
INSERT INTO note_importance(
    note_id, importance_score, in_degree, out_degree,
    connectivity_score, trust_score, freshness_score, lifecycle_weight, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
ON CONFLICT(note_id) DO UPDATE SET ...
```

Returns `dict[str, ImportanceSignal]`.

### Wire into Layer 2b job in `jobs.py`

Must run after `compute_note_trust_stats` and `compute_note_freshness`:

```python
if config.maintenance.importance_scoring_enabled:
    importance_signals = compute_importance_scores(connection)
    stats["importance_notes_scored"] = len(importance_signals)
```

### Retrieval integration: `src/second_brain/retrieval.py`

After building candidate note IDs from BM25/semantic/graph passes, load importance
scores:

```python
importance_map = {}
if note_ids:
    rows = connection.execute(
        f"SELECT note_id, importance_score FROM note_importance WHERE note_id IN ({placeholders})",
        note_ids,
    ).fetchall()
    importance_map = {str(r["note_id"]): float(r["importance_score"] or 0.0) for r in rows}
```

Include `importance_score` in the `retrieval_hit_features` insert alongside
existing `bm25_score`, `semantic_score`, `graph_score`, `freshness_score`,
`trust_score`. Add `importance_score REAL` column to `retrieval_hit_features`
table via a new migration if not already present — check schema first.

The importance score is not applied as a direct score multiplier in the current
rerank formula. It is logged as a feature so the weekly `train_rerank_model` can
learn from it over time.

## Weight rationale

| Component | Weight | Rationale |
|---|---|---|
| connectivity | 0.35 | In-degree = others depend on this note. High signal. |
| trust | 0.25 | Retrieval usefulness is direct evidence of value. |
| freshness | 0.25 | Stale notes should rank lower regardless of connectivity. |
| lifecycle | 0.15 | Canonical > verified > draft reflects curation confidence. |

Weights sum to 1.0. All components are already normalized to [0, 1] before
combining.

## Milestones

1. `migrations/019_note_importance.sql` exists and applies cleanly.
2. `ImportanceSignal` dataclass in `maintenance.py`.
3. `compute_importance_scores` returns correct scores for seeded notes.
4. Layer 2b job calls `compute_importance_scores` after trust/freshness.
5. `note_importance` table populated after job run.
6. `retrieval.py` loads importance scores and includes in `retrieval_hit_features`.

## Verification

- Test: seed two notes — one canonical with high in-degree and high trust, one
  draft with no edges and low trust. Verify canonical note has higher importance.
- Test: freshness_score contribution: note with staleness_score=0.9 has
  freshness_score=0.1, pulls importance down.
- Test: note absent from `note_trust` and `note_freshness` → defaults (0.5) used,
  no error.
- Test: `retrieval_hit_features` row after retrieval includes `importance_score`.
- Integration: Layer 2b job dry-run completes; `importance_scoring_enabled=False`
  skips the function cleanly.

## Out of scope

- Using `importance_score` directly in the rerank formula — it is logged as a
  feature and learned from, not hardcoded as a multiplier.
- Per-type importance weights — all note types use the same formula for now.
- Importance-based retrieval cutoffs — the existing limit parameters govern
  candidate set size.

# Knowledge Maintenance Phase 2: Graph Maintenance — Bulk Rebuild

## Goal

Run a full-wiki graph rebuild daily, repairing any drift that has accumulated
since individual notes were last promoted. Prune stale edges and orphan nodes.
Wire into the Layer 2b job before trust/freshness computation.

## Context

`rebuild_note_graph(connection, note)` in `graph.py` exists and is called at
promotion time for each promoted note. It rebuilds graph edges for that single
note from its `entity_relations` field. It does not scan the wiki.

Graph drift accumulates from:
- Notes manually edited on disk without going through promotion
- Notes deleted or renamed outside of the promotion writer
- Entity relation changes in frontmatter that postdate the last promotion

The `compute_note_freshness` function in `trust.py` reads `graph_edges` to count
`linked_incident_count` and `newer_evidence_count`. If edges are stale, freshness
signals are wrong. Graph rebuild must run before these computations.

## Scope

### New module: `src/second_brain/maintenance.py`

All new maintenance functions live here. Phases 2–5 each add functions to this
module.

### `GraphRebuildResult` dataclass in `maintenance.py`

```python
@dataclass(slots=True)
class GraphRebuildResult:
    notes_scanned: int
    notes_rebuilt: int
    stale_edges_pruned: int
    orphan_nodes_pruned: int
```

### `rebuild_wiki_graph(connection, config, *, dry_run=False) -> GraphRebuildResult`

Algorithm:

1. Fetch all active notes: `SELECT id, body_path FROM notes WHERE status IS NULL
   OR status NOT IN ('retired', 'archived')`.
2. For each note row, resolve `body_path` against the workspace root.
3. Skip if the file does not exist on disk (log as warning, do not prune DB row —
   that is vault sync's responsibility).
4. Call `parse_note(path)` → `NoteRecord`.
5. If not `dry_run`: call `rebuild_note_graph(connection, record)` (already
   exists in `graph.py` — reuse directly, do not duplicate).
6. Increment `notes_rebuilt`.
7. After all notes rebuilt:
   - Prune stale edges: `DELETE FROM graph_edges WHERE target_id NOT IN
     (SELECT id FROM graph_nodes)`. Count rows deleted → `stale_edges_pruned`.
   - Prune orphan nodes: `DELETE FROM graph_nodes WHERE id NOT IN
     (SELECT source_note_id FROM graph_edges) AND id NOT IN
     (SELECT target_id FROM graph_edges) AND node_type = 'entity'`.
     (Never prune note-type nodes — that is sync's job.)
     Count rows deleted → `orphan_nodes_pruned`.
8. Return `GraphRebuildResult`.

All DB writes wrapped in a single `with connection:` transaction.

### `detect_orphan_nodes(connection) -> list[str]`

Returns note IDs with no outbound AND no inbound edges. Used for stats and
surfacing in health reporting (Phase 5).

```python
def detect_orphan_nodes(connection) -> list[str]:
    rows = connection.execute("""
        SELECT n.id
        FROM notes n
        WHERE (n.status IS NULL OR n.status NOT IN ('retired', 'archived'))
          AND NOT EXISTS (SELECT 1 FROM graph_edges ge WHERE ge.source_note_id = n.id)
          AND NOT EXISTS (SELECT 1 FROM graph_edges ge WHERE ge.target_id = n.id)
    """).fetchall()
    return [str(row["id"]) for row in rows]
```

### Wire into Layer 2b job in `jobs.py`

The Layer 2b maintenance job calls `rebuild_wiki_graph` first, before
`compute_note_trust_stats` and `compute_note_freshness`.

```python
graph_result = rebuild_wiki_graph(connection, config, dry_run=dry_run)
stats["graph_notes_rebuilt"] = graph_result.notes_rebuilt
stats["graph_stale_edges_pruned"] = graph_result.stale_edges_pruned
stats["graph_orphan_nodes_pruned"] = graph_result.orphan_nodes_pruned
```

Import `rebuild_wiki_graph` from `maintenance.py` in the job implementation.

## Performance note

`rebuild_note_graph` calls `DELETE FROM graph_edges WHERE source_note_id = ?`
then re-inserts for each note. For a wiki with N notes, this is O(N) deletes and
O(E) inserts where E is the total edge count. For wikis under ~10,000 notes this
is fast enough for a 3 AM job. If performance becomes a problem, add a
content_hash comparison to skip unchanged notes.

## Milestones

1. `maintenance.py` module exists with `GraphRebuildResult` and
   `rebuild_wiki_graph`.
2. `detect_orphan_nodes` exists and returns correct IDs.
3. Layer 2b job calls `rebuild_wiki_graph` before trust/freshness.
4. Stats dict includes `graph_notes_rebuilt`, `graph_stale_edges_pruned`,
   `graph_orphan_nodes_pruned`.
5. Dry-run produces no DB writes.

## Verification

- Test: seed two notes A→B via entity_relations; delete note B from DB (simulate
  drift); run `rebuild_wiki_graph`; verify edge A→B still exists (node B is an
  entity node now, not a note node).
- Test: manually insert a stale edge in `graph_edges` pointing to a note_id that
  does not exist; run rebuild; verify edge pruned.
- Test: note with no entity_relations has no outbound edges after rebuild;
  `detect_orphan_nodes` returns its ID.
- Test: dry_run=True → DB unchanged after rebuild.
- Integration: Layer 2b job dry-run completes without error, stats include graph keys.

## Out of scope

- Incremental rebuild (only process notes changed since last run) — full rebuild
  first, optimize later.
- HTTP projection or lease-based graph inference — edges come from frontmatter
  `entity_relations` only.
- Lifecycle state changes (Phase 3).

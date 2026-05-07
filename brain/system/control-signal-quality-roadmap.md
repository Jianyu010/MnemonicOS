# Roadmap: Control Signal Quality and Retrieval Performance

## Goal

MnemonicOS captures rich session history but agents still loop during debugging because retrieved memory delivers no actionable guidance. This is not a data-volume problem — it is a signal quality problem. Memory exists; it is not shaped for agent consumption.

This roadmap addresses three compounding failures:

1. **Retrieved notes carry no compact control signal** — agents parse full bodies, get noise, loop.
2. **Sufficiency assessment is blind to content quality** — empty incidents return `sufficient_current`, agents trust them, get nothing.
3. **Episode notes lack shared `chain_id`** — cross-session arc never loads; agents retrieve fragments, not context.

Fixing these is foundational to all future agentic performance on MnemonicOS. A 15-hour debugging loop on the calculator task is the concrete evidence.

---

## Evidence

**Calculator task (ai firm, 2026-04-11):**
- Task: "build calculator with special functions"
- Duration: ~15 hours, 7+ revision cycles
- With MnemonicOS: agent recalled GUI requirement from history (memory working). Agent still looped on debugging (signal failing).
- Root cause: retrieved notes (incidents, procedures) had empty Cause/Fix/steps and no chain_id. Agent read them as `sufficient_current`, trusted them, got nothing actionable.

**arXiv 2604.15097 (Gene-based procedural memory):**
- LLMs perform worse with verbose documentation-style procedural memory.
- Compact "Gene" format (`WHEN [trigger] → DO [action]`) outperforms full procedure logs.
- Failure info best encoded as concise warnings, not event timelines.

---

## Architecture Context

- Retrieval hit payload exposes: `summary`, `excerpt`, `status`, `lifecycle_state`. NOT arbitrary frontmatter fields.
- `sufficient_current` determined by `freshness_state` (date-based), not content quality (`retrieval.py:321-326`).
- `_load_same_episode_context()` queries `notes` table (wiki), not raw sessions. Chain fires only when promoted canonical notes share a `chain_id` (`runtime.py:441-500`).
- Parser ignores arbitrary frontmatter fields not in `NoteRecord`. New fields (`control_summary`, `warnings`) must flow through `summary:` for v1 or require DB schema migration for v2.

---

## Scope

This is a **performance improvement refactor**, not a bug fix. Changes affect:

- Note templates (schema)
- Existing wiki procedures and incidents (content backfill)
- Assessment logic in Python bridge (`assess_retrieval_payload`)
- New canonical episode notes for the calculator task
- Promotion criteria documentation

No schema migrations required for v1. All changes use existing supported fields and ingestion paths.

---

## Implementation Plan

### Phase 1: Gene strings in `summary:` field (zero schema change)

**Problem:** `summary:` field currently empty or copies title. Gene string slot wasted.

**Fix:**
- Set `summary: "WHEN [trigger] → DO [action] (NOT [anti-pattern])"` on all 10 procedures and all 4 incidents.
- `summary` already reaches retrieval hit payload and `assess_retrieval_payload()`.
- Gene string is the first signal agents see.

**Also add optional field stubs to templates (metadata-only for now):**
```yaml
# Optional fields — not yet in NoteRecord; add to frontmatter for future parser support:
# control_summary: "WHEN ... → DO ..."
# warnings:
#   - "AVOID: ..."
# resolution_state: open|verified|failed
# recovery_path: ""
```

**Files:**
- `brain/templates/procedure.md`
- `brain/templates/incident.md`
- `brain/wiki/procedures/drafts/*.md` (10 files) — set `summary:` to gene string
- `brain/wiki/incidents/*.md` (4 files) — set `summary:`, add `resolution_state:`, `recovery_path:`

---

### Phase 2: Content quality gate in `assess_retrieval_payload()`

**Problem:** Empty-body note = `sufficient_current` if fresh. Agent trusts empty guidance.

**Fix (marker-based heuristic — no schema change required):**

Downgrade from `current` to `partial` (with `recommendation: reuse_with_verification`) if:
- `summary == title` or `summary` is empty, AND
- `excerpt` word count < 20 AND excerpt contains none of: `WHEN`, `DO`, `Steps`, `Cause`, `Fix`, `Avoid`

This is heuristic, not structured. Safe to implement in Python bridge.

**Future (v2):** Add `content_quality_score` from parser to `NoteRecord`; use in scoring directly.

**File:** Bridge Python module containing `assess_retrieval_payload()`.

---

### Phase 3: Calculator episode — promote 3 canonical notes, share chain_id

**Problem:** Calculator raw sessions have no promoted wiki notes with shared `chain_id`. `_load_same_episode_context()` can't fire. Agent retrieves unlinked fragments.

**Fix:**

Generate `chain_id: trace/ai-firm-calculator-special-functions-2026-04-10`.

Create 3 canonical notes:

**`brain/wiki/concepts/calculator-phase1-gui-setup-success.md`**
```yaml
type: concept
chain_id: trace/ai-firm-calculator-special-functions-2026-04-10
summary: "Calculator task Phase 1: GUI shell built successfully using tkinter. Layout with buttons confirmed working."
source_refs: [session/2026-04-11-...]
```

**`brain/wiki/concepts/calculator-phase2-special-functions-partial.md`**
```yaml
type: concept
chain_id: trace/ai-firm-calculator-special-functions-2026-04-10
summary: "Calculator task Phase 2: Standard arithmetic functional. Special function buttons not wired — missing handler registration."
source_refs: [session/2026-04-11-...]
```

**`brain/wiki/incidents/calculator-phase3-environment-failure.md`**
```yaml
type: incident
chain_id: trace/ai-firm-calculator-special-functions-2026-04-10
resolution_state: failed
success_path: ""
recovery_path: "Run tests from project root with python -m pytest. Verify sys.path includes project root. Install package editably with pip install -e . if using module imports."
linked_procedures:
  - procedure/use-run-python-exit-1
  - procedure/use-read-file-write-file-reading-existing-files-before-rewriting-them-is-a-reusable-safe-edit-pattern
summary: "WHEN ModuleNotFoundError during testing → verify working dir and sys.path before retrying (NOT same run_python call unchanged)"
source_refs: [session/2026-04-11-012340-...]
```

Also update existing `brain/wiki/incidents/failure-at-phase-3-*.md`:
- Add `chain_id: trace/ai-firm-calculator-special-functions-2026-04-10`
- Fill Cause: "Tests run from wrong working directory; module not on sys.path"
- Fill Fix: "Run from project root; use python -m pytest or pip install -e ."
- Set `resolution_state: failed`, `recovery_path:` as above

After creating files: run sync/daemon to index into `notes` table.

---

### Phase 4: Fill Cause/Fix/linked_procedures on all 4 incidents

For each open incident: write actual content into Cause, Fix, Prevention sections. Do not leave EMPTY or "NEEDS_REVISION".

**Calculator incident**: covered in Phase 3.

**Orchestrator silent-drop incident** (`orchestrator-finish-task-silently-dropped-summaries-fixed-2026-04-16.md`):
- Cause: "cli.py and server.py called finish_task but not ingest_session after it. Summaries routed through Stop hook path were dropped."
- Fix: "Added ingest_session() call in cli.py and server.py after finish_task() when decision != 'skip'."
- resolution_state: verified

Update `lifecycle_state: resolved` and `status: closed` on this incident after filling.

---

### Phase 5: Define promotion criteria in MEMORY.md

**Current state:** No promotion rules. MEMORY.md says "default to archive unless promotion rules clearly apply" — rules absent.

**Add to `brain/system/MEMORY.md`:**

```
## Promotion Criteria for Procedures

A procedure note is eligible for promotion from `drafts/` to `active/` when ALL of:
- source_refs >= 5 (observed multiple times)
- summary field is a gene string (not empty, not copy of title)
- At least 1 confirmed retrieval in task_outcomes with outcome_type != misleading

Convergence alone is insufficient — noise convergence (many runs of same buggy command) does not indicate quality.

Promotion steps:
1. Move file from wiki/procedures/drafts/ to wiki/procedures/active/
2. Update lifecycle_state: canonical in frontmatter
3. Run sync (daemon or manual) to re-index
4. Verify: run mnemonic_context.sh with relevant query, confirm note appears
```

**Also add to `brain/system/ACTIVE.md`:**
- Maintenance item: periodic review of procedures with trust_score data from retrieval_hits table.

---

## Verification

| Step | Check |
|------|-------|
| Phase 1 complete | Run `mnemonic_context.sh "ModuleNotFoundError"` → gene string appears in returned summary |
| Phase 2 complete | Leave one incident with empty summary → retrieval returns `partial`, not `sufficient_current` |
| Phase 3 complete | Run `mnemonic_context.sh "calculator module not found"` → `same_episode` channel notes load alongside primary hit |
| Phase 4 complete | Read any incident → Cause + Fix populated, not empty |
| Phase 5 complete | Move 1 procedure to active/ → sync → retrieval returns it at equal or higher score |

---

## What NOT to Change (v1)

| Temptation | Why not |
|-----------|---------|
| Add control_summary to NoteRecord/DB | Requires schema migration + sync changes. Use summary: for now. |
| Filter retrieval by lifecycle_state: draft | draft not penalized today; only retired filtered. Unintended side effect. |
| Block postflight on resolution | Creates memory gaps. Add resolution_state field, allow open. |
| Applicability filter in shell script | Brittle. Future: scoring boost layer, not shell grep. |
| Truncate procedure bodies | Destroys auditability. Add gene front via summary:, keep body. |
| Direct DB UPDATE | Bypasses index consistency. Use sync/daemon path. |

---

## Files to Modify

| File | Change |
|------|--------|
| `brain/templates/procedure.md` | Add optional field stubs in frontmatter comment |
| `brain/templates/incident.md` | Add resolution_state, recovery_path, warnings stubs |
| `brain/system/MEMORY.md` | Promotion criteria |
| `brain/system/ACTIVE.md` | Maintenance task item |
| `brain/wiki/procedures/drafts/*.md` (10) | Set `summary:` to gene string |
| `brain/wiki/incidents/*.md` (4) | Set `summary:`, fill Cause/Fix, add resolution_state, linked_procedures |
| `brain/wiki/incidents/failure-at-phase-3-*.md` | Add chain_id, full Cause/Fix, resolution_state: failed |
| 3 new wiki episode notes | Phase 1/2/3 calculator notes with shared chain_id |
| Bridge `assess_retrieval_payload()` | Marker-based content quality gate |

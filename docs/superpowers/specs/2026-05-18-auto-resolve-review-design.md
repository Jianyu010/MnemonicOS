# Auto-Resolve Review — Design Spec

**Date:** 2026-05-18
**Status:** Approved for implementation
**Branch:** fix/batched-window-extraction

---

## 1. Context and Motivation

The promotion pipeline routes candidates to `wiki/review/` when any of four
conditions hold:

| Reason code | Trigger |
|---|---|
| `low_confidence` | `candidate.confidence == "low"` |
| `type_collision` | Same title slug exists in wiki under a different note type |
| `wiki_conflict` | Multiple plausible wiki matches, top similarity score < 0.90 |
| `contested_lifecycle` | Top matching wiki note has `lifecycle_state = "contested"` |

Human reviewers currently see only the candidate body and reason code. They have
no access to competing note content, similarity scores, or a suggested action.
The queue accumulates faster than humans can clear it, and each decision is
effectively made blind.

This spec introduces a daemon job that resolves the majority of review items
automatically using an LLM, appending its analysis back to the review file only
when it cannot act with sufficient confidence — so human reviewers receive richer
context when they do intervene.

---

## 2. Architecture Overview

```
*/5 * * * *   process_promotion_queue    → routes candidates to wiki/review/
*/10 * * * *  wiki_auto_resolve_review   ← NEW: drains wiki/review/
```

The new job runs every ten minutes, just after the promotion queue has had a
chance to produce fresh review items. For each review file it finds:

1. Re-run `run_gate()` on the candidate payload — if now failing, reject and delete.
2. Re-run `decide()` — if the wiki has changed since routing such that the
   decision is no longer `"review"`, auto-promote via `approve_review_item()`.
3. If still `decision="review"`, dispatch to the reason-specific LLM resolver.
4. LLM resolver returns `action`, `reasoning`, `confidence` (0.0–1.0).
5. `confidence >= 0.75` and `action != "human"`:
   - `approve` → call `approve_review_item()`
   - `reject`  → call `reject_review_item()`
6. `confidence < 0.75` or `action = "human"` → append `## LLM Analysis` block to
   the review file on disk (human fallback enrichment).

No new DB migrations are required. The auto-resolver re-derives all conflict
context at resolve time by re-running `decide()`, which also handles stale
review items whose conflict has since resolved.

---

## 3. New Module: `auto_resolver.py`

**Location:** `src/second_brain/promotion/auto_resolver.py`

### 3.1 Public Interface

```python
@dataclass
class AutoResolveRunResult:
    approved: int = 0
    rejected: int = 0
    auto_promoted: int = 0   # decide() returned non-review on re-evaluation
    human_fallback: int = 0
    errors: list[str] = field(default_factory=list)

    def to_stats(self) -> dict[str, int]: ...


def run_auto_resolve(
    config: AppConfig,
    *,
    llm_fn: Callable[[str], str] | None = None,
    dry_run: bool = False,
) -> AutoResolveRunResult:
    """Drain wiki/review/ items using LLM arbitration."""
```

### 3.2 LLM Callable

`_make_llm_fn(config)` wraps `build_llm_provider(config)` to expose a simple
`(prompt: str) -> str` interface. Four provider branches:

- **`ollama`** — calls `_ollama_generate()` directly (bypasses extraction packet format)
- **`openai_compatible`** — single-message chat request to `/chat/completions`
- **`cli_command`** — appends prompt as `argv[-1]`
- **`fixture`** — returns canned `{"action": "human", ...}` for tests

### 3.3 Finding Candidate Payload

Review file frontmatter always includes `source_refs: [candidate:{id}, ...]`.
`_load_candidate_payload()` parses this, queries `extraction_candidates.payload_json`,
and returns `(candidate_id, payload)` or `None` if not found.

### 3.4 Per-Item Processing Flow

```
load_candidate_payload → gate check → decide() re-evaluation
                                           ↓
                              still review? → LLM resolver
                                           ↓
                           confidence ≥ 0.75 → approve/reject
                           confidence < 0.75 → append LLM Analysis
```

`FileExistsError` from `approve_review_item` (target already in wiki) → fall
through to `reject_review_item`.

`FileNotFoundError` from either op → human already acted, treat as no-op.

---

## 4. Per-Reason LLM Resolution

All resolvers request this JSON schema:
```json
{
  "action": "approve | reject | human",
  "target_note_id": "<note_id or null>",
  "reasoning": "<one sentence>",
  "confidence": 0.0
}
```

Parse failures return `{"action": "human", "confidence": 0.0, "reasoning": "parse_error"}`.

### `low_confidence`
Context: candidate body + top-1 similar wiki note body (via `find_existing_candidates`).
- `approve` → candidate contains verifiable knowledge not fully covered by wiki
- `reject` → candidate is vague, duplicative, or unsupported
- `human` → cannot determine from text alone

### `type_collision`
Context: candidate body + conflicting note(s) body (different type, same slug).
- `approve` → genuinely distinct concept despite slug overlap
- `reject` → near-duplicate or lower quality than existing note
- `human` → distinction is ambiguous

### `wiki_conflict`
Context: candidate body + all conflict note bodies + similarity scores from
re-running `find_existing_candidates`.
- `approve` + `target_note_id` → update best matching note
- `approve` + `target_note_id=null` → candidate is new
- `reject` → candidate is redundant
- `human` → cannot determine best match

### `contested_lifecycle`
Context: candidate body + contested note body.
- `approve` → candidate corroborates or clearly supersedes contested note
- `reject` → no new evidence
- `human` → relationship is genuinely ambiguous

---

## 5. Human Fallback Enrichment

When `action == "human"` or `confidence < 0.75`, append to review file (atomic):

```markdown
## LLM Analysis

**Action suggested:** {action}
**Confidence:** {confidence:.2f}
**Reasoning:** {reasoning}
```

Idempotent: skip if `## LLM Analysis` already present.

---

## 6. Daemon Job Registration

**`daemon_jobs.py`:** add `"wiki_auto_resolve_review": "*/10 * * * *"` to
`_DEFAULT_SCHEDULES`.

**`jobs.py`:** add `_wiki_auto_resolve_review_job()` (mirrors
`_process_promotion_queue_job` pattern) and branch in `run_job()`.

---

## 7. Testing

File: `tests/test_auto_resolver.py`

Required test cases:
1. `test_gate_fail_rejects_immediately`
2. `test_decide_changed_auto_promotes`
3. `test_llm_approve_calls_approve_review_item`
4. `test_llm_reject_calls_reject_review_item`
5. `test_low_confidence_human_fallback` (confidence < 0.75)
6. `test_human_action_writes_fallback`
7. `test_idempotent_fallback_append`
8. `test_missing_candidate_payload_is_error`
9. `test_dry_run_makes_no_changes`
10. `test_make_llm_fn_fixture`

Pattern: `tempfile.TemporaryDirectory` + `_config()` + `connect_db` + `run_migrations`.
Plant review files and `extraction_candidates` rows directly.

---

## 8. Edge Cases

- `_parse_llm_response` never raises — returns `human/0.0/parse_error` on failure
- LLM timeout/OSError → treat as `human/0.0`
- `notes.body_path` missing → substitute `"(body unavailable)"` in prompt
- `## LLM Analysis` idempotency — check before appending
- Empty review dir → job exits cleanly with all counters at 0
- Concurrent human action → `FileNotFoundError` caught as no-op

---

## 9. Critical Files

| File | Change |
|---|---|
| `src/second_brain/promotion/auto_resolver.py` | **NEW** |
| `src/second_brain/jobs.py` | Add job handler + `run_job()` branch |
| `src/second_brain/daemon_jobs.py` | Add schedule entry |
| `tests/test_auto_resolver.py` | **NEW** |
| `brain/system/INGESTION-PATH-GOVERNANCE-DESIGN.md` | Update layers diagram + descriptions |
| `brain/system/architecture.md` | Update Mermaid diagram |
| `docs/daemon-and-socket.md` | Add job to schedule table |
| `docs/cli-reference.md` | Add job to table, note in review section |
| `README.md` | Update invariant text + ingestion flow diagram |

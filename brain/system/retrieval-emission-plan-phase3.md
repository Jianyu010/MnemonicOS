# Retrieval Emission Phase 3: Markdown Renderer and Full Text Presentation

## Goal

Render a stable `## Memory Context` block that presents the selected assessed
packet in full enough form for an agent or human caller, while respecting Stage
5 budget decisions and Stage 6 branch/profile decisions.

## Scope

- Markdown section order
- selected memory presentation
- full text vs summary/reference policy
- prompt-injection-safe boundaries
- compact but complete directive header

## Section Order

```text
<!-- MnemonicOS context marker -->
## Memory Context
- retrieval_state
- completion_state
- recommendation
- verification_action
- emission_profile

### Primary Memory / Selected Path
### Same Episode / Functional Path
### Related Context
### Archive Evidence
### Warnings and Contradictions
### Follow-up Query
### Retry Cutoff
### Retry Trace
### Deferred Context
### Provenance
<!-- end marker -->
```

Sections with no content should be omitted, except `no_memory` and
`warning_only`, which must render explicit messages.

## Full Text Policy

Stage 7 should render all selected packet text made available by Stage 5. It
should not broaden the packet by following body paths, links, relations, or
archive references unless a future body resolver is explicitly designed as a
read-only, budget-bound adapter.

When only summaries/body references are present, Markdown should say enough for
the caller to act and retain the reference path/provenance needed to inspect the
source.

## Follow-up Query Text

When `followup_query_policy = immediate_focused_requery`, Markdown should
include a short, explicit instruction such as:

```text
Found partial memory:
- incident/auth-rollout-failure explains the failed rollout.
- decision/auth-rollout-decision explains the pivot.

Missing required role:
- success_resolution

Suggested focused MnemonicOS query:
"auth rollout recovery success_resolution chain_id:trace/auth-rollout-recovery procedure/recover-auth-rollout"
```

When `followup_query_policy = on_first_failure`, Markdown should describe the
condition:

```text
If this path fails, ask:
"<original task> failure diagnosis / deferred branch: branch/foo"
```

The renderer must not present the suggested query as already executed.
The suggested query should prefer stable anchors over free prose:

- original task intent
- missing role
- stable note IDs
- `chain_id`
- `branch_id`
- key error signature, if any
- relevant relation target, if known

If an automatic focused retry already ran and hit a hard cutoff, Markdown should
say that plainly:

```text
Memory remains partial after focused retry.
Cutoff: no_new_ids
Do not keep querying automatically.
Proceed with available memory or do fresh external research.
```

When an automatic retry ran, Markdown should present the final/focused pass
first, then a compact trace:

```text
Retry Trace:
- Pass 1 query_id=123 emitted focused query for missing role success_resolution.
- Pass 2 query_id=124 found 1 new memory: procedure/recover-auth-rollout.
```

If the retry found nothing new:

```text
Retry Trace:
- Pass 1 query_id=123 emitted focused query for missing role success_resolution.
- Pass 2 query_id=124 found no new memory. Cutoff: no_new_ids.
```

## Caution

Markdown is often injected into agent prompts. Keep machine-readable markers,
avoid decorative prose, label raw archive evidence, and preserve warning text
without converting it into confident instructions.

## Verification

- markdown contains all directive summary fields
- markdown and JSON select the same memory IDs
- no-memory packet renders explicit no-memory text
- archive-only packet labels archive evidence as raw/non-canonical
- overabundant packet renders deferred context summary without leaking all
  branches
- immediate focused requery packet renders a suggested query and no nested
  second-pass result
- suggested query contains stable anchors when they are available
- retry cutoff packet renders a hard stop notice
- automatic retry packet renders final pass plus compact retry trace

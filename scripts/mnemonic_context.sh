#!/usr/bin/env bash
# mnemonic_context.sh — run hybrid retrieval and print compact or structured context

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUERY=""
QUERY_TYPE_HINT=""
TOP_K=""
DRY_RUN=0
EMIT_JSON=0
INCLUDE_BODY=0
BODY_CHARS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --query-type-hint) QUERY_TYPE_HINT="$2"; shift 2 ;;
    --top-k)           TOP_K="$2"; shift 2 ;;
    --dry-run)         DRY_RUN=1; shift ;;
    --json)            EMIT_JSON=1; shift ;;
    --include-body)    INCLUDE_BODY=1; shift ;;
    --body-chars)      BODY_CHARS="$2"; shift 2 ;;
    -*)                echo "Unknown option: $1" >&2; exit 1 ;;
    *)                 QUERY="$1"; shift ;;
  esac
done

if [[ -z "$QUERY" ]]; then
  echo "Usage: mnemonic_context.sh \"<query>\" [--query-type-hint <hint>] [--top-k <N>] [--json] [--include-body] [--body-chars <N>]" >&2
  exit 1
fi

if [[ -f "$REPO_ROOT/config/memory.toml" ]]; then
  CONFIG="$REPO_ROOT/config/memory.toml"
else
  CONFIG="$REPO_ROOT/config/memory.example.toml"
fi
# Test/agent isolation: explicit config path overrides repo default.
CONFIG="${MNEMO_CONFIG:-$CONFIG}"

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

if [[ $DRY_RUN -eq 1 ]]; then
  if [[ $EMIT_JSON -eq 1 ]]; then
    printf '{\n  "query": "%s",\n  "retrieval_state": "dry_run",\n  "recommendation": "reuse_with_verification"\n}\n' "$QUERY"
  else
    echo "<!-- MnemonicOS context [DRY RUN] -->"
    echo "<!-- query: $QUERY -->"
    echo "<!-- query-type-hint: ${QUERY_TYPE_HINT:-none} -->"
    echo "<!-- top-k: ${TOP_K:-default} -->"
  fi
  exit 0
fi

RETRIEVE_CMD=("${CLI[@]}" retrieve "$QUERY" --json)
[[ -n "$QUERY_TYPE_HINT" ]] && RETRIEVE_CMD+=(--query-type-hint "$QUERY_TYPE_HINT")
[[ -n "$TOP_K" ]] && RETRIEVE_CMD+=(--top-k "$TOP_K")
[[ $INCLUDE_BODY -eq 1 ]] && RETRIEVE_CMD+=(--include-body)
[[ -n "$BODY_CHARS" ]] && RETRIEVE_CMD+=(--body-chars "$BODY_CHARS")

cd "$REPO_ROOT"
RAW="$("${RETRIEVE_CMD[@]}" 2>/dev/null)" || RAW=""
if [[ -z "$RAW" ]]; then
  if [[ $EMIT_JSON -eq 1 ]]; then
    echo '{"query":"","retrieval_state":"insufficient_no_hit","recommendation":"research_fresh","hit_ids":[],"context_markdown":""}'
  else
    echo "<!-- MnemonicOS: retrieval error, continuing without context -->"
  fi
  exit 0
fi

env RAW_PAYLOAD="$RAW" PYTHONPATH="$REPO_ROOT/src" python3 - "$QUERY" "$EMIT_JSON" <<'PYEOF'
import json
import os
import sys

from second_brain.bridge import assess_retrieval_payload

query = sys.argv[1]
emit_json = sys.argv[2] == "1"
payload = json.loads(os.environ["RAW_PAYLOAD"])
assessment = assess_retrieval_payload(payload, query)

hits = payload.get("hits", [])
pinned = payload.get("pinned_paths", [])

lines = ["<!-- MnemonicOS context block — do not edit -->", "## Memory Context", ""]
lines.append(f"- retrieval_state: `{assessment.state}`")
lines.append(f"- recommendation: `{assessment.recommendation}`")
if assessment.current_ids:
    lines.append(f"- current_hits: {', '.join(assessment.current_ids[:5])}")
if assessment.stale_ids:
    lines.append(f"- stale_hits: {', '.join(assessment.stale_ids[:5])}")
if assessment.contested_ids:
    lines.append(f"- contested_hits: {', '.join(assessment.contested_ids[:5])}")
lines.append("")

if pinned:
    lines.append("### Pinned")
    lines.append("")
    for path in pinned:
        lines.append(f"- {path}")
    lines.append("")

suppressed_by_gate = int(payload.get("suppressed_by_gate_count", 0))
passed_gate = int(payload.get("passed_gate_count", 0))
suppression_rate = suppressed_by_gate / (suppressed_by_gate + passed_gate) if (suppressed_by_gate + passed_gate) > 0 else 0.0
min_relevance = 0.25

if hits:
    lines.append("### Relevant Notes")
    lines.append("")
    for hit in hits[:8]:
        title = hit.get("title", "untitled")
        note_type = hit.get("type", "")
        status = hit.get("status") or ""
        freshness = hit.get("freshness_state") or ""
        summary = (hit.get("summary") or hit.get("excerpt") or "").replace("\n", " ")
        channels = ", ".join(hit.get("channels", []))
        relevance_score = hit.get("relevance_score")
        quality_score = hit.get("quality_score")
        flags = []
        if freshness in {"stale", "suspect", "contested"}:
            flags.append(freshness)
        if status in {"superseded", "archived"}:
            flags.append(status)
        flag_text = f" [{' | '.join(flags)}]" if flags else ""
        lines.append(f"**{title}** ({note_type}){flag_text}")
        if summary:
            lines.append(f"  {summary[:220]}")
        if channels:
            lines.append(f"  _via {channels}_")
        if note_type == "archive_chunk":
            lines.append(f"  score={hit.get('final_score', 0.0):.3f} [rank-based]")
        elif relevance_score is not None and quality_score is not None:
            lines.append(f"  relevance={relevance_score:.2f} quality={quality_score:.2f}")
        lines.append("")
    if suppression_rate > 0.50 and suppressed_by_gate > 0:
        lines.append(f"<!-- retrieval note: >50% of candidates were gate-suppressed; consider broadening query terms if context seems thin -->")
        lines.append("")
else:
    lines.append("### Relevant Notes")
    lines.append("")
    if suppressed_by_gate > 0:
        lines.append(
            f"- No notes met the relevance threshold. {suppressed_by_gate} candidate{'s' if suppressed_by_gate != 1 else ''} were recalled but"
            f" suppressed (relevance_score < {min_relevance}). Query likely outside MnemonicOS corpus."
            f" Proceed with own reasoning; do not treat prior hits as authoritative."
        )
    else:
        lines.append("- No relevant MnemonicOS notes found.")
    lines.append("")

lines.append("<!-- end MnemonicOS context -->")
context_markdown = "\n".join(lines)

if emit_json:
    json.dump(
        {
            "query": query,
            "retrieval_state": assessment.state,
            "recommendation": assessment.recommendation,
            "hit_ids": assessment.hit_ids,
            "stale_ids": assessment.stale_ids,
            "contested_ids": assessment.contested_ids,
            "current_ids": assessment.current_ids,
            "partial_ids": assessment.partial_ids,
            "context_markdown": context_markdown,
            "raw_result": payload,
        },
        sys.stdout,
        indent=2,
    )
    print()
else:
    print(context_markdown)
PYEOF

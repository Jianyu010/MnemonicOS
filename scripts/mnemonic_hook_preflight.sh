#!/usr/bin/env bash
# mnemonic_hook_preflight.sh — Claude Code UserPromptSubmit hook
#
# Reads the user prompt from Claude Code's hook JSON (stdin), decides if
# retrieval is warranted, and if so injects a MnemonicOS context block.
#
# Claude Code hook protocol:
#   Input (stdin): JSON with at minimum { "prompt": "..." }
#   Output (stdout): JSON { "context": "..." } to prepend context, or empty to no-op
#   Exit: always 0 — never block the prompt on retrieval errors

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS="$REPO_ROOT/scripts"

# Read stdin JSON — extract the prompt field
HOOK_JSON=$(cat)
PROMPT=$(echo "$HOOK_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('prompt',''))" 2>/dev/null) || PROMPT=""

# Nothing to do for empty prompts
if [[ -z "$PROMPT" ]]; then
  exit 0
fi

# Check if retrieval is warranted
if ! "$SCRIPTS/mnemonic_should_retrieve.sh" --task "$PROMPT" 2>/dev/null; then
  exit 0
fi

# Derive a query-type hint from the prompt
QUERY_HINT=""
PROMPT_LOWER=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]')
if echo "$PROMPT_LOWER" | grep -qiE 'how (to|do|should)|procedure|step[s]?|workflow'; then
  QUERY_HINT="how_to"
elif echo "$PROMPT_LOWER" | grep -qiE 'decision|why (did|does|was|is)|chose|choice|reason'; then
  QUERY_HINT="decision"
elif echo "$PROMPT_LOWER" | grep -qiE 'who (is|was|owns|works)|person|team|contact'; then
  QUERY_HINT="who_where_relation"
fi

# Run retrieval
RETRIEVE_ARGS=("$PROMPT")
[[ -n "$QUERY_HINT" ]] && RETRIEVE_ARGS+=(--query-type-hint "$QUERY_HINT")

PAYLOAD=$("$SCRIPTS/mnemonic_context.sh" "${RETRIEVE_ARGS[@]}" --json 2>/dev/null) || PAYLOAD=""

# If no meaningful context was returned, no-op
if [[ -z "$PAYLOAD" ]]; then
  exit 0
fi

CONTEXT=$(echo "$PAYLOAD" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('context_markdown',''))" 2>/dev/null) || CONTEXT=""
STATE=$(echo "$PAYLOAD" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('retrieval_state',''))" 2>/dev/null) || STATE=""
if [[ -z "$CONTEXT" || "$STATE" == "insufficient_no_hit" ]]; then
  exit 0
fi

# Output Claude Code hook response: inject context
python3 -c "
import json, sys
context = sys.argv[1]
print(json.dumps({'context': context}))
" "$CONTEXT"

exit 0

#!/usr/bin/env bash
# mnemonic_should_retrieve.sh — heuristic: should memory retrieval happen for this task?
#
# Usage:
#   mnemonic_should_retrieve.sh --task "<task text>" [--after-failure]
#
# Exit: 0 = retrieve recommended; 1 = skip retrieval
# Output: brief reason to stderr (for logging)

set -euo pipefail

TASK=""
AFTER_FAILURE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task)          TASK="$2"; shift 2 ;;
    --after-failure) AFTER_FAILURE=1; shift ;;
    *)               echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TASK" ]]; then
  echo "Usage: mnemonic_should_retrieve.sh --task \"<task text>\" [--after-failure]" >&2
  exit 1
fi

# After first meaningful failure: always retrieve
if [[ $AFTER_FAILURE -eq 1 ]]; then
  echo "retrieve: forced after failure" >&2
  exit 0
fi

TASK_LOWER=$(echo "$TASK" | tr '[:upper:]' '[:lower:]')

# Patterns that strongly suggest retrieval is valuable
RETRIEVE_PATTERNS=(
  # Project/repo memory
  'project' 'repo' 'codebase' 'architecture' 'design' 'structure'
  # Decisions and procedures
  'decision' 'procedure' 'protocol' 'policy' 'process' 'workflow'
  'how (do|to|should|can)' 'best (way|practice|approach)' 'should i'
  # Technical work
  'implement' 'build' 'create' 'add feature' 'refactor' 'migrate'
  'debug' 'fix' 'error' 'fail' 'broken' 'issue' 'bug' 'problem'
  'setup' 'config' 'install' 'deploy' 'test' 'ci' 'pipeline'
  # Memory and history
  'remember' 'recall' 'what did' 'what was' 'history' 'context'
  'previously' 'last time' 'before' 'why (did|does|is|was|are)'
  # People and concepts
  'who (is|was|are|owns|works)' 'what is' 'concept' 'definition'
  'explain' 'compare' 'research' 'learn' 'teach me' 'how does' 'how .* work'
  'why does' 'rocket' 'algorithm' 'technology' 'framework'
  # Incidents and reviews
  'incident' 'outage' 'postmortem' 'review' 'retro'
)

# Patterns that strongly suggest retrieval is NOT needed
SKIP_PATTERNS=(
  '^(ls|pwd|cd|echo|cat|head|tail|date|time|which|whoami|hostname|uname|df|du|ps|top|man)(\s|$)'
  '^git (status|log|diff|show|branch|tag)(\s|$)'
  '--version$' '--help$' '-h$'
  '^(clear|reset|exit|quit|bye)$'
  'print hello' 'hello world'
)

# Check skip patterns first
for pattern in "${SKIP_PATTERNS[@]}"; do
  if echo "$TASK_LOWER" | grep -qiE "$pattern" 2>/dev/null; then
    echo "skip: matched trivial pattern '$pattern'" >&2
    exit 1
  fi
done

# Check retrieve patterns
for pattern in "${RETRIEVE_PATTERNS[@]}"; do
  if echo "$TASK_LOWER" | grep -qiE "$pattern" 2>/dev/null; then
    echo "retrieve: matched pattern '$pattern'" >&2
    exit 0
  fi
done

# Default: skip for short/simple prompts, retrieve for longer ones
WORD_COUNT=$(echo "$TASK" | wc -w | tr -d ' ')
if [[ $WORD_COUNT -ge 8 ]]; then
  echo "retrieve: long prompt ($WORD_COUNT words), defaulting to retrieve" >&2
  exit 0
fi

echo "skip: no retrieval pattern matched, short prompt" >&2
exit 1

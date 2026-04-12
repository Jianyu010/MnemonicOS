#!/usr/bin/env bash
# mnemonic_should_writeback.sh — heuristic: is this summary worth ingesting?
#
# Usage:
#   mnemonic_should_writeback.sh --mode failure|success|knowledge --summary-file <path>
#
# Exit: 0 = ingest recommended; 1 = skip ingest
# Output: brief reason to stderr

set -euo pipefail

MODE=""
SUMMARY_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)         MODE="$2";         shift 2 ;;
    --summary-file) SUMMARY_FILE="$2"; shift 2 ;;
    *)              echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$MODE" || -z "$SUMMARY_FILE" ]]; then
  echo "Usage: mnemonic_should_writeback.sh --mode failure|success|knowledge --summary-file <path>" >&2
  exit 1
fi

if [[ ! -f "$SUMMARY_FILE" ]]; then
  echo "Error: summary file not found: $SUMMARY_FILE" >&2
  exit 1
fi

SUMMARY_LOWER=$(tr '[:upper:]' '[:lower:]' < "$SUMMARY_FILE")
WORD_COUNT=$(wc -w < "$SUMMARY_FILE" | tr -d ' ')

# Reject empty or near-empty summaries regardless of mode
if [[ $WORD_COUNT -lt 20 ]]; then
  echo "skip: summary too short ($WORD_COUNT words)" >&2
  exit 1
fi

case "$MODE" in
  failure)
    # Permissive for failures: ingest if it describes any real problem
    FAILURE_SIGNALS=(
      'error' 'fail' 'wrong' 'incorrect' 'broken' 'bug' 'issue' 'problem'
      'retry' 'tried' 'attempt' 'changed approach' 'rollback' 'revert'
      'diagnosis' 'root cause' 'missing' 'stale' 'outdated' 'assumed'
      'unexpected' 'surprised' 'misunderstood' 'contradiction'
    )
    for signal in "${FAILURE_SIGNALS[@]}"; do
      if echo "$SUMMARY_LOWER" | grep -qiE "\b$signal\b" 2>/dev/null; then
        echo "ingest: failure signal '$signal' found" >&2
        exit 0
      fi
    done
    echo "skip: no meaningful failure signal detected in summary" >&2
    exit 1
    ;;

  success)
    # Strict for successes: ingest only if clearly reusable
    REUSABLE_SIGNALS=(
      'procedure' 'workflow' 'pattern' 'best practice' 'command' 'flag'
      'confirmed' 'verified' 'resolved' 'fixed' 'works when' 'use.*instead'
      'learned' 'takeaway' 'going forward' 'future' 'next time' 'remember'
      'decision' 'architecture' 'design choice' 'tradeoff'
      'replaces' 'supersedes' 'updated' 'corrects'
    )
    for signal in "${REUSABLE_SIGNALS[@]}"; do
      if echo "$SUMMARY_LOWER" | grep -qiE "$signal" 2>/dev/null; then
        echo "ingest: reusable success signal '$signal' found" >&2
        exit 0
      fi
    done
    # Also ingest if summary contains a memory block
    if grep -q '```memory' "$SUMMARY_FILE" 2>/dev/null; then
      echo "ingest: explicit memory block found" >&2
      exit 0
    fi
    echo "skip: no reusable learning detected in success summary" >&2
    exit 1
    ;;

  knowledge)
    KNOWLEDGE_SIGNALS=(
      'concept' 'definition' 'explain' 'compare' 'how does' 'what is'
      'architecture' 'algorithm' 'framework' 'technology' 'research'
      'fresh_sources_checked' 'mnemos_retrieval_state' 'writeback_intent'
    )
    for signal in "${KNOWLEDGE_SIGNALS[@]}"; do
      if echo "$SUMMARY_LOWER" | grep -qiE "$signal" 2>/dev/null; then
        echo "ingest: knowledge signal '$signal' found" >&2
        exit 0
      fi
    done
    if grep -q '```memory' "$SUMMARY_FILE" 2>/dev/null; then
      echo "ingest: explicit memory block found" >&2
      exit 0
    fi
    echo "skip: no durable knowledge signal detected" >&2
    exit 1
    ;;

  *)
    echo "Error: --mode must be 'failure', 'success', or 'knowledge'" >&2
    exit 1
    ;;
esac

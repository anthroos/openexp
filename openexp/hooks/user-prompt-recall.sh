#!/bin/bash
# OpenExp UserPromptSubmit hook — contextual recall on every user message.
#
# Takes user's prompt, searches for relevant memories,
# returns as additionalContext so Claude has experience before acting.
#
# Fast path: skip trivial prompts (< 10 chars, confirmations)
set -uo pipefail

# Resolve paths relative to this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENEXP_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
PYTHON="$OPENEXP_DIR/.venv/bin/python3"
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

# Read stdin
INPUT=$(cat)
PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // ""')
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"')

# --- Fast exit for trivial prompts ---
PROMPT_LEN=${#PROMPT}
if [ "$PROMPT_LEN" -lt 10 ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit"}}'
  exit 0
fi

# Skip common confirmations
PROMPT_LOWER=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
case "$PROMPT_LOWER" in
  "yes"|"no"|"ok"|"continue"|"go"|"done"|"next"|"thanks"|"thank you")
    echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit"}}'
    exit 0
    ;;
esac

# Truncate prompt for search query (max 300 chars)
QUERY="${PROMPT:0:300}"

# --- Detect experience from prompt + search memories ---
cd "$OPENEXP_DIR"
export OPENEXP_TMPFILE="$TMPFILE"
export OPENEXP_SESSION_ID="$SESSION_ID"
"$PYTHON" -c "
import json, sys, os
sys.path.insert(0, '.')
from openexp.core.config import Q_CACHE_PATH
from openexp.core.q_value import QCache
from openexp.core import direct_search
from openexp.core.experience import detect_experience_from_prompt, save_session_experience

q = QCache()
q.load(Q_CACHE_PATH)

query = sys.stdin.read().strip()
if not query:
    sys.exit(1)

# Auto-detect experience from prompt keywords
experience = detect_experience_from_prompt(query)
session_id = os.environ.get('OPENEXP_SESSION_ID', '')
if experience != 'default' and session_id and session_id != 'unknown':
    save_session_experience(session_id, experience)

tmpfile = os.environ['OPENEXP_TMPFILE']
context = direct_search.search_memories(query=query, limit=5, q_cache=q, experience=experience)
json.dump({'context': context, 'experience': experience}, open(tmpfile, 'w'), default=str)
" <<< "$QUERY" 2>/dev/null

if [ ! -s "$TMPFILE" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit"}}'
  exit 0
fi

# --- Parse and format ---
ALL_IDS=""
ALL_SCORES=""

CONTEXT_TEXT=""
if jq -e '.context.results | length > 0' "$TMPFILE" >/dev/null 2>&1; then
  CONTEXT_TEXT=$(jq -r '.context.results[] |
    "[q=\(.q_value // 0.5 | . * 100 | floor / 100)] \(.memory[:250])"' "$TMPFILE")
  ALL_IDS=$(jq -r '[.context.results[].id] | join(",")' "$TMPFILE")
  ALL_SCORES=$(jq -r '[.context.results[].score] | map(tostring) | join(",")' "$TMPFILE")
fi

# No results
if [ -z "$CONTEXT_TEXT" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit"}}'
  exit 0
fi

# --- Log retrieval for reward loop ---
if [ -n "$ALL_IDS" ] && [ "$SESSION_ID" != "unknown" ]; then
  ("$PYTHON" -m openexp.cli log-retrieval \
    --session-id "$SESSION_ID" --query "${QUERY:0:200}" \
    --memory-ids "$ALL_IDS" --scores "$ALL_SCORES" 2>/dev/null) &
fi

# --- Read detected experience ---
DETECTED_EXP=$(jq -r '.experience // "default"' "$TMPFILE" 2>/dev/null)

# --- Build output using jq for safe string handling ---
REMINDER="\n\nREMINDER: Before starting this task, call search_memory with a targeted query. Hooks recalled the above automatically, but you must also do a manual targeted search for complex tasks."

# Show experience label if non-default
EXP_LABEL=""
if [ "$DETECTED_EXP" != "default" ]; then
  EXP_LABEL=" [experience: $DETECTED_EXP]"
fi

jq -n \
  --arg context "$CONTEXT_TEXT" \
  --arg reminder "$REMINDER" \
  --arg exp_label "$EXP_LABEL" \
  '{
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: ("## Recall: Context" + $exp_label + "\n" + $context + $reminder + "\n")
    }
  }'

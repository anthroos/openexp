#!/bin/bash
# OpenExp SessionStart hook — smart context injection.
#
# Searches Qdrant for relevant memories based on working directory
# and injects them as additionalContext at session start.
set -uo pipefail

# Resolve paths relative to this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENEXP_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
PYTHON="$OPENEXP_DIR/.venv/bin/python3"
SESSIONS_DIR="$HOME/.openexp/sessions"
TMPDIR_HOOK=$(mktemp -d)
chmod 700 "$TMPDIR_HOOK"
trap 'rm -rf "$TMPDIR_HOOK"' EXIT

# Read stdin (Claude Code passes session JSON)
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // "/tmp"')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')
PROJECT=$(basename "$CWD")

# --- Build smart query ---
TODAY_Q=$(date +%Y-%m-%d)
DAY_Q=$(date +%A)

# Get last session context
LAST_SESSION_FILE=$(ls -t "$SESSIONS_DIR"/*.md 2>/dev/null | head -1)
LAST_CONTEXT=""
if [ -n "$LAST_SESSION_FILE" ] && [ -f "$LAST_SESSION_FILE" ]; then
  LAST_CONTEXT=$(sed -n '/^## What was done/,/^## /p' "$LAST_SESSION_FILE" 2>/dev/null \
    | grep '^\-' \
    | grep -v '=' \
    | grep -v 'import ' \
    | grep -v '(.*)' \
    | head -3 \
    | tr '\n' ' ' | cut -c1-200)
fi

# Build query based on context
if [ "$PROJECT" = "$(whoami)" ] || [ "$PROJECT" = "~" ]; then
  QUERY="active projects pending follow-ups $DAY_Q $LAST_CONTEXT"
else
  QUERY="$PROJECT $LAST_CONTEXT"
fi

# --- Search memories ---
cd "$OPENEXP_DIR"
export OPENEXP_TMPDIR="$TMPDIR_HOOK"
# Resolve experience: project .openexp.yaml → env var → default
EXPERIENCE="${OPENEXP_EXPERIENCE:-default}"
if [ -f "$CWD/.openexp.yaml" ]; then
  PROJECT_EXP=$(OPENEXP_CWD="$CWD" python3 -c "
import yaml, os
d=yaml.safe_load(open(os.path.join(os.environ['OPENEXP_CWD'], '.openexp.yaml')))
print(d.get('experience',''))
" 2>/dev/null)
  [ -n "$PROJECT_EXP" ] && EXPERIENCE="$PROJECT_EXP"
fi
"$PYTHON" -c "
import json, sys, os
sys.path.insert(0, '.')
from openexp.core.config import Q_CACHE_PATH
from openexp.core.q_value import QCache
from openexp.core import direct_search

q = QCache()
q.load(Q_CACHE_PATH)

query = sys.stdin.read().strip()
if not query:
    sys.exit(1)

tmpdir = os.environ['OPENEXP_TMPDIR']
experience = os.environ.get('OPENEXP_EXPERIENCE', 'default')
context = direct_search.search_memories(query=query, limit=10, q_cache=q, experience=experience)
json.dump({'context': context}, open(os.path.join(tmpdir, 'results.json'), 'w'), default=str)
" <<< "$QUERY" 2>/dev/null

RESULTS_FILE="$TMPDIR_HOOK/results.json"
if [ ! -f "$RESULTS_FILE" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"SessionStart"}}'
  exit 0
fi

# --- Parse results ---
ALL_IDS=""
ALL_SCORES=""

CONTEXT_TEXT=""
if jq -e '.context.results | length > 0' "$RESULTS_FILE" >/dev/null 2>&1; then
  CONTEXT_TEXT=$(jq -r '.context.results[] |
    "[sim=\(.hybrid_score // .score | . * 100 | floor / 100)] [q=\(.q_value // 0.5 | . * 100 | floor / 100)] \(.memory[:200])"' "$RESULTS_FILE")
  ALL_IDS=$(jq -r '[.context.results[].id] | join(",")' "$RESULTS_FILE")
  ALL_SCORES=$(jq -r '[.context.results[].score] | map(tostring) | join(",")' "$RESULTS_FILE")
fi

# No results — exit cleanly
if [ -z "$CONTEXT_TEXT" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"SessionStart"}}'
  exit 0
fi

# --- Log retrieval for Q-learning reward loop ---
if [ -n "$ALL_IDS" ] && [ "$SESSION_ID" != "unknown" ]; then
  ("$PYTHON" -m openexp.cli log-retrieval \
    --session-id "$SESSION_ID" --query "$QUERY" \
    --memory-ids "$ALL_IDS" --scores "$ALL_SCORES" 2>/dev/null) &
fi

# --- Build output using jq for safe string handling ---
TODAY=$(date +%Y-%m-%d)
DAY=$(date +%A)

jq -n \
  --arg project "$PROJECT" \
  --arg day "$DAY" \
  --arg today "$TODAY" \
  --arg context "$CONTEXT_TEXT" \
  '{
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: ("# OpenExp Memory (Q-value ranked)\nQuery: " + $project + " | " + $day + " " + $today + "\n\n## Relevant Context\n" + $context + "\n")
    }
  }'

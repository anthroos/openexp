#!/bin/bash
# OpenExp SessionEnd hook — ingest transcript + extract decisions.
#
# Two steps (async, background):
#   1. Extract decisions from transcript (Opus 4.6 via extract_decisions)
#   2. Ingest full transcript into Qdrant (every user + assistant message)
#
# Both run in background so they don't block session exit.
set -uo pipefail

# Guard: skip if running inside extraction subprocess (prevents recursion)
if [ "${OPENEXP_EXTRACT_RUNNING:-}" = "1" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"SessionEnd"}}'
  exit 0
fi

# Resolve paths relative to this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENEXP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$OPENEXP_DIR/.venv/bin/python3"

INGEST_LOG="$HOME/.openexp/ingest.log"

# Read stdin (Claude Code passes session JSON)
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')
CWD=$(echo "$INPUT" | jq -r '.cwd // ""')

# Nothing to do without a session ID
if [ "$SESSION_ID" = "unknown" ] || [ "$SESSION_ID" = "null" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"SessionEnd"}}'
  exit 0
fi

SESSION_SHORT="${SESSION_ID:0:8}"

# Return hook output immediately (don't block session exit)
echo '{"hookSpecificOutput":{"hookEventName":"SessionEnd"}}'

# -- Background: find transcript and process --
(
  cd "$OPENEXP_DIR"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: starting for session $SESSION_SHORT" >> "$INGEST_LOG"

  # Resolve experience
  EXPERIENCE="${OPENEXP_EXPERIENCE:-default}"
  if [ -n "$CWD" ] && [ -f "$CWD/.openexp.yaml" ]; then
    PROJECT_EXP=$(OPENEXP_CWD="$CWD" "$PYTHON" -c "
import yaml, os
d=yaml.safe_load(open(os.path.join(os.environ['OPENEXP_CWD'], '.openexp.yaml')))
print(d.get('experience',''))
" 2>/dev/null)
    [ -n "$PROJECT_EXP" ] && EXPERIENCE="$PROJECT_EXP"
  fi

  # Find transcript file
  TRANSCRIPT_FILE=""
  CLAUDE_PROJECTS_DIR="$HOME/.claude/projects"
  if [ -d "$CLAUDE_PROJECTS_DIR" ]; then
    for project_dir in "$CLAUDE_PROJECTS_DIR"/*/; do
      [ -d "$project_dir" ] || continue
      # Try exact session ID match first (filename = session_id.jsonl)
      if [ -f "${project_dir}${SESSION_ID}.jsonl" ]; then
        TRANSCRIPT_FILE="${project_dir}${SESSION_ID}.jsonl"
        break
      fi
      # Fallback: grep inside files
      for f in "$project_dir"*.jsonl; do
        [ -f "$f" ] || continue
        if grep -q "\"sessionId\":\"$SESSION_ID\"" "$f" 2>/dev/null; then
          TRANSCRIPT_FILE="$f"
          break 2
        fi
      done
    done
  fi

  if [ -z "$TRANSCRIPT_FILE" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: no transcript found for $SESSION_SHORT" >> "$INGEST_LOG"
    exit 0
  fi

  export OPENEXP_TRANSCRIPT_FILE="$TRANSCRIPT_FILE"
  export OPENEXP_SESSION_ID="$SESSION_ID"
  export OPENEXP_EXPERIENCE="$EXPERIENCE"

  # Step 1: Extract decisions
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: extracting decisions from $TRANSCRIPT_FILE" >> "$INGEST_LOG"
  "$PYTHON" -c "
import sys, json, os, logging
sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO)
from pathlib import Path
from openexp.ingest.extract_decisions import extract_and_store

result = extract_and_store(
    transcript_path=Path(os.environ['OPENEXP_TRANSCRIPT_FILE']),
    session_id=os.environ['OPENEXP_SESSION_ID'],
    experience=os.environ['OPENEXP_EXPERIENCE'],
)
print(json.dumps(result, default=str))
" >> "$INGEST_LOG" 2>&1

  # Step 2: Ingest full transcript (idempotent — skips if already ingested)
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: ingesting transcript for $SESSION_SHORT" >> "$INGEST_LOG"
  "$PYTHON" -c "
import sys, json, os, logging
sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO)
from pathlib import Path
from openexp.ingest.transcript import ingest_transcript

result = ingest_transcript(
    transcript_path=Path(os.environ['OPENEXP_TRANSCRIPT_FILE']),
    session_id=os.environ['OPENEXP_SESSION_ID'],
    experience=os.environ['OPENEXP_EXPERIENCE'],
)
print(json.dumps(result, default=str))
" >> "$INGEST_LOG" 2>&1

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: done for $SESSION_SHORT" >> "$INGEST_LOG"
) &
disown

#!/bin/bash
# OpenExp SessionEnd hook — closes the Q-learning loop.
#
# Two phases:
#   1. SYNC  — Generate session summary .md from observations JSONL
#   2. ASYNC — Trigger ingest + reward (nohup background)
#
# This is the critical piece: without it, observations never get ingested,
# reward never gets computed, and Q-values stay at 0.5 forever.
set -uo pipefail

# Resolve paths relative to this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENEXP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$OPENEXP_DIR/.venv/bin/python3"

OBS_DIR="$HOME/.openexp/observations"
SESSIONS_DIR="$HOME/.openexp/sessions"
INGEST_LOG="$HOME/.openexp/ingest.log"

# Read stdin (Claude Code passes session JSON)
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')

# Nothing to do without a session ID
if [ "$SESSION_ID" = "unknown" ] || [ "$SESSION_ID" = "null" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"SessionEnd"}}'
  exit 0
fi

SESSION_SHORT="${SESSION_ID:0:8}"
TODAY=$(date +%Y-%m-%d)

mkdir -p "$SESSIONS_DIR"

# -- Phase 1: Generate session summary (synchronous, fast) --

# Find observations for this session
OBS_FILE=""
for f in "$OBS_DIR"/observations-*.jsonl; do
  [ -f "$f" ] || continue
  if grep -q "\"session_id\":\"$SESSION_ID\"" "$f" 2>/dev/null || \
     grep -q "\"session_id\": \"$SESSION_ID\"" "$f" 2>/dev/null; then
    OBS_FILE="$f"
    break
  fi
done

# Also check partial session ID match (Claude Code sometimes uses short IDs)
if [ -z "$OBS_FILE" ]; then
  for f in "$OBS_DIR"/observations-*.jsonl; do
    [ -f "$f" ] || continue
    if grep -q "$SESSION_SHORT" "$f" 2>/dev/null; then
      OBS_FILE="$f"
      break
    fi
  done
fi

SUMMARY_FILE="$SESSIONS_DIR/${TODAY}-${SESSION_SHORT}.md"

# Only generate if we found observations and summary doesn't exist yet
if [ -n "$OBS_FILE" ] && [ ! -f "$SUMMARY_FILE" ]; then
  "$PYTHON" -c "
import json, sys
from pathlib import Path
from collections import OrderedDict

session_id = '$SESSION_ID'
obs_file = Path('$OBS_FILE')
today = '$TODAY'

observations = []
for line in obs_file.read_text().splitlines():
    if not line.strip():
        continue
    try:
        obs = json.loads(line)
    except json.JSONDecodeError:
        continue
    sid = obs.get('session_id', '')
    if session_id in sid or sid.startswith(session_id[:8]):
        observations.append(obs)

if not observations:
    sys.exit(0)

# Extract unique summaries (deduplicate)
seen = set()
summaries = []
for obs in observations:
    s = obs.get('summary', '').strip()
    if s and s not in seen:
        seen.add(s)
        summaries.append(s)

# Extract files changed
files = OrderedDict()
for obs in observations:
    fp = obs.get('context', {}).get('file_path', '')
    tool = obs.get('tool', '')
    if fp and tool in ('Write', 'Edit'):
        files[Path(fp).name] = fp

# Detect project
project = observations[0].get('project', 'unknown') if observations else 'unknown'

# Build markdown
md = f'# Session Summary: {today}\n\n'
md += f'**Session ID:** {session_id[:8]}\n'
md += f'**Project:** {project}\n\n'

md += '## What was done\n'
for s in summaries[:30]:  # cap at 30 entries
    md += f'- {s}\n'

if files:
    md += '\n## Files changed\n'
    for name, full in files.items():
        md += f'- {full}\n'

Path('$SUMMARY_FILE').write_text(md)
" 2>/dev/null
fi

# -- Phase 2: Trigger ingest + reward (async, background) --

# nohup ensures ingest runs even after Claude Code exits
(
  cd "$OPENEXP_DIR"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: starting ingest for session $SESSION_SHORT" >> "$INGEST_LOG"

  "$PYTHON" -m openexp.cli ingest --session-id "$SESSION_ID" >> "$INGEST_LOG" 2>&1
  EXIT_CODE=$?

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: ingest finished (exit=$EXIT_CODE)" >> "$INGEST_LOG"
) &
disown

# Return hook output immediately (don't block session exit)
echo '{"hookSpecificOutput":{"hookEventName":"SessionEnd"}}'

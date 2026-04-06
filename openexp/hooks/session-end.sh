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

# Guard: skip if running inside extraction subprocess (prevents recursion)
if [ "${OPENEXP_EXTRACT_RUNNING:-}" = "1" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"SessionEnd"}}'
  exit 0
fi

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
CWD=$(echo "$INPUT" | jq -r '.cwd // ""')

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
  export OPENEXP_SESSION_ID="$SESSION_ID"
  export OPENEXP_OBS_FILE="$OBS_FILE"
  export OPENEXP_TODAY="$TODAY"
  export OPENEXP_SUMMARY_FILE="$SUMMARY_FILE"
  "$PYTHON" -c "
import json, os, sys
from pathlib import Path
from collections import OrderedDict

session_id = os.environ['OPENEXP_SESSION_ID']
obs_file = Path(os.environ['OPENEXP_OBS_FILE'])
today = os.environ['OPENEXP_TODAY']

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

Path(os.environ['OPENEXP_SUMMARY_FILE']).write_text(md)
" 2>/dev/null
fi

# -- Phase 2: Trigger ingest + reward (async, background) --

# nohup ensures ingest runs even after Claude Code exits
(
  cd "$OPENEXP_DIR"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: starting ingest for session $SESSION_SHORT" >> "$INGEST_LOG"

  # Resolve experience: auto-detected (from prompts) → project .openexp.yaml → env var → default
  EXPERIENCE="${OPENEXP_EXPERIENCE:-default}"
  # Check if experience was auto-detected during this session
  AUTO_EXP=$("$PYTHON" -c "
import sys
sys.path.insert(0, '.')
from openexp.core.experience import get_session_experience
exp = get_session_experience('$SESSION_ID')
print(exp or '')
" 2>/dev/null)
  if [ -n "$AUTO_EXP" ]; then
    EXPERIENCE="$AUTO_EXP"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: using auto-detected experience '$EXPERIENCE'" >> "$INGEST_LOG"
  elif [ -n "$CWD" ] && [ -f "$CWD/.openexp.yaml" ]; then
    PROJECT_EXP=$(OPENEXP_CWD="$CWD" python3 -c "
import yaml, os
d=yaml.safe_load(open(os.path.join(os.environ['OPENEXP_CWD'], '.openexp.yaml')))
print(d.get('experience',''))
" 2>/dev/null)
    [ -n "$PROJECT_EXP" ] && EXPERIENCE="$PROJECT_EXP"
  fi
  export OPENEXP_EXPERIENCE="$EXPERIENCE"
  # Phase 2a: Full ingest + session reward (ingests ALL pending obs, rewards THIS session)
  "$PYTHON" -m openexp.cli ingest --session-id "$SESSION_ID" >> "$INGEST_LOG" 2>&1
  EXIT_CODE=$?
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: ingest finished (exit=$EXIT_CODE)" >> "$INGEST_LOG"

  # Phase 2b: Fallback reward — if obs were already ingested (by launchd or prior session),
  # raw_obs was empty and reward didn't fire above. Read obs from JSONL directly.
  # Guard: skip if reward was already applied for this session (idempotency).
  "$PYTHON" -c "
import json, sys, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
session_id = '$SESSION_ID'
data_dir = Path.home() / '.openexp' / 'data'
reward_log = data_dir / 'reward_log.jsonl'

# Check if reward already applied for this session
if reward_log.exists():
    for line in reward_log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ctx = entry.get('context', {})
        if isinstance(ctx, dict) and session_id in ctx.get('session_id', ''):
            print(f'Reward already applied for session {session_id[:8]}, skipping fallback')
            sys.exit(0)

# No reward yet — read observations from JSONL and compute
from openexp.ingest.reward import compute_session_reward, reward_retrieved_memories, _build_session_reward_context
from openexp.core.experience import get_active_experience

obs_dir = Path.home() / '.openexp' / 'observations'
session_obs = []
for f in sorted(obs_dir.glob('observations-*.jsonl')):
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obs = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = obs.get('session_id', '')
        if session_id in sid or sid.startswith(session_id[:8]):
            session_obs.append(obs)

if not session_obs:
    print(f'No observations found for session {session_id[:8]}')
    sys.exit(0)

experience = get_active_experience()
reward = compute_session_reward(session_obs, weights=experience.session_reward_weights)
if reward == 0.0:
    print(f'Session {session_id[:8]}: neutral reward, skipping')
    sys.exit(0)

reward_ctx = _build_session_reward_context(session_obs, reward)
updated = reward_retrieved_memories(
    session_id, reward,
    experience=experience.name,
    reward_context=reward_ctx,
    reward_memory_types=experience.reward_memory_types,
)
print(f'Fallback reward={reward:.2f} applied to {updated} retrieved memories ({len(session_obs)} obs)')
" >> "$INGEST_LOG" 2>&1
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: fallback reward finished" >> "$INGEST_LOG"

  # Phase 2c: Decision extraction from transcript (Opus 4.6)
  # This is the most valuable step — extracts DECISIONS, not actions.
  # Derive project dir from CWD (Claude Code stores transcripts per-project)
  if [ -n "$CWD" ]; then
    PROJECT_KEY=$(echo "$CWD" | tr '/' '-' | sed 's/^-//')
  else
    PROJECT_KEY=$(echo "$PWD" | tr '/' '-' | sed 's/^-//')
  fi
  TRANSCRIPT_DIR="$HOME/.claude/projects/$PROJECT_KEY"
  TRANSCRIPT_FILE=""
  # Find transcript file for this session
  for f in "$TRANSCRIPT_DIR"/*.jsonl; do
    [ -f "$f" ] || continue
    if grep -q "\"sessionId\":\"$SESSION_ID\"" "$f" 2>/dev/null; then
      TRANSCRIPT_FILE="$f"
      break
    fi
  done
  # Also try partial match
  if [ -z "$TRANSCRIPT_FILE" ]; then
    for f in "$TRANSCRIPT_DIR"/*.jsonl; do
      [ -f "$f" ] || continue
      if grep -q "$SESSION_SHORT" "$f" 2>/dev/null; then
        TRANSCRIPT_FILE="$f"
        break
      fi
    done
  fi

  if [ -n "$TRANSCRIPT_FILE" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: extracting decisions from $TRANSCRIPT_FILE" >> "$INGEST_LOG"
    "$PYTHON" -c "
import sys, json, logging
sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO)
from pathlib import Path
from openexp.ingest.extract_decisions import extract_and_store

result = extract_and_store(
    transcript_path=Path('$TRANSCRIPT_FILE'),
    session_id='$SESSION_ID',
    experience='$EXPERIENCE',
)
print(json.dumps(result, default=str))
" >> "$INGEST_LOG" 2>&1
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: decision extraction finished" >> "$INGEST_LOG"
  else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SessionEnd: no transcript found for session $SESSION_SHORT" >> "$INGEST_LOG"
  fi

  # Cleanup session experience file
  "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
from openexp.core.experience import cleanup_session_experience
cleanup_session_experience('$SESSION_ID')
" 2>/dev/null
) &
disown

# Return hook output immediately (don't block session exit)
echo '{"hookSpecificOutput":{"hookEventName":"SessionEnd"}}'

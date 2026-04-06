#!/bin/bash
# OpenExp PostToolUse hook — capture observations from tool calls.
#
# Records tool usage (Write, Edit, Bash, etc.) as observations
# for later ingestion into Qdrant via the ingest pipeline.
set -uo pipefail

OBS_DIR="$HOME/.openexp/observations"
mkdir -p "$OBS_DIR"

# Read stdin (Claude Code passes tool call JSON)
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // "unknown"')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')
CWD=$(echo "$INPUT" | jq -r '.cwd // ""')
PROJECT=$(basename "${CWD:-/tmp}")

# Skip read-only tools — not worth storing
case "$TOOL" in
  Read|Glob|Grep|WebSearch|WebFetch|AskUserQuestion)
    echo '{"hookSpecificOutput":{"hookEventName":"PostToolUse"}}'
    exit 0
    ;;
esac

# Extract relevant info based on tool type
SUMMARY=""
FILE_PATH=""
OBS_TYPE="feature"

case "$TOOL" in
  Write)
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')
    SUMMARY="Wrote file: $(basename "$FILE_PATH")"
    ;;
  Edit)
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')
    SUMMARY="Edited file: $(basename "$FILE_PATH")"
    ;;
  Bash)
    CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""' | head -c 200)
    SUMMARY="Ran: $CMD"
    ;;
  NotebookEdit)
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.notebook_path // ""')
    SUMMARY="Edited notebook: $(basename "$FILE_PATH")"
    ;;
  *)
    SUMMARY="Used tool: $TOOL"
    ;;
esac

# Skip empty summaries
if [ -z "$SUMMARY" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"PostToolUse"}}'
  exit 0
fi

# Generate observation ID
OBS_ID="obs-$(date +%Y%m%d)-$(openssl rand -hex 4)"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Write observation to JSONL
OBS_FILE="$OBS_DIR/observations-$(date +%Y-%m-%d).jsonl"
jq -cn \
  --arg id "$OBS_ID" \
  --arg timestamp "$TIMESTAMP" \
  --arg session_id "$SESSION_ID" \
  --arg project "$PROJECT" \
  --arg type "$OBS_TYPE" \
  --arg tool "$TOOL" \
  --arg summary "$SUMMARY" \
  --arg file_path "$FILE_PATH" \
  '{
    id: $id,
    timestamp: $timestamp,
    session_id: $session_id,
    project: $project,
    type: $type,
    tool: $tool,
    summary: $summary,
    tags: [],
    context: {
      file_path: $file_path
    }
  }' | if command -v flock >/dev/null 2>&1; then
    flock "$OBS_FILE.lock" tee -a "$OBS_FILE" >/dev/null
  else
    # mkdir-based locking for macOS (no flock available)
    LOCKDIR="$OBS_FILE.lock"
    while ! mkdir "$LOCKDIR" 2>/dev/null; do sleep 0.01; done
    cat >> "$OBS_FILE"
    rmdir "$LOCKDIR"
  fi

echo '{"hookSpecificOutput":{"hookEventName":"PostToolUse"}}'

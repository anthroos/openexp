#!/bin/bash
# OpenExp PostToolUse hook: capture observations from tool usage.
# Runs after every tool call — must be fast and never block.
# Captures Write, Edit, Bash; skips read-only commands; redacts secrets.
set -uo pipefail

OBSERVATIONS_DIR="${OPENEXP_OBSERVATIONS_DIR:-$HOME/.openexp/observations}"
mkdir -p "$OBSERVATIONS_DIR" 2>/dev/null || true

TODAY=$(date +%Y-%m-%d)
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')
CWD=$(echo "$INPUT" | jq -r '.cwd // "/tmp"')
PROJECT=$(basename "$CWD")

case "$TOOL_NAME" in
  Write|Edit|Bash) ;;
  *) exit 0 ;;
esac

FILE_PATH=""
COMMAND=""
SUMMARY=""

case "$TOOL_NAME" in
  Write)
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
    [ -z "$FILE_PATH" ] && exit 0
    case "$FILE_PATH" in
      *.env|*.env.*|*credentials.json|*token.json|*secret*|*.pem|*.key|*/.ssh/*) exit 0 ;;
    esac
    SUMMARY="Wrote $(basename "$FILE_PATH")"
    ;;
  Edit)
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
    [ -z "$FILE_PATH" ] && exit 0
    case "$FILE_PATH" in
      *.env|*.env.*|*credentials.json|*token.json|*secret*|*.pem|*.key|*/.ssh/*) exit 0 ;;
    esac
    SUMMARY="Edited $(basename "$FILE_PATH")"
    ;;
  Bash)
    COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
    [ -z "$COMMAND" ] && exit 0
    BASE_CMD=$(echo "$COMMAND" | sed 's|^/[^ ]*/||')
    case "$BASE_CMD" in
      ls*|cat\ *|pwd*|echo\ *|head\ *|tail\ *|wc\ *|which\ *|type\ *|cd\ *|test\ *|\[\ *|find\ *) exit 0 ;;
    esac
    if echo "$COMMAND" | grep -qiE '(export.*TOKEN|export.*SECRET|export.*KEY|export.*PASSWORD)'; then
      SUMMARY="Ran: [env variable setup - REDACTED]"
      COMMAND=""
    else
      SUMMARY="Ran: ${COMMAND:0:300}"
    fi
    ;;
esac

[ -z "$SUMMARY" ] && exit 0

# Redact tokens/passwords from summary
SUMMARY=$(echo "$SUMMARY" | sed -E 's/(token|password|api_key|secret|credential)["\047 :=]+[^ "\047]{8,}/\1=[REDACTED]/gi')
SUMMARY=$(echo "$SUMMARY" | sed -E 's/Bearer [A-Za-z0-9_\.\-\/+=]+/Bearer [REDACTED]/g')

OBS_ID="obs-$(date +%Y%m%d)-$(openssl rand -hex 4)"

if [ -n "$FILE_PATH" ]; then
  CONTEXT=$(jq -n --arg fp "$FILE_PATH" '{"file_path": $fp}')
elif [ -n "$COMMAND" ]; then
  CONTEXT=$(jq -n --arg cmd "${COMMAND:0:300}" '{"command": $cmd}')
else
  CONTEXT="{}"
fi

OBSERVATION=$(jq -cn \
  --arg id "$OBS_ID" \
  --arg timestamp "$TIMESTAMP" \
  --arg session_id "$SESSION_ID" \
  --arg type "feature" \
  --arg tool "$TOOL_NAME" \
  --arg summary "$SUMMARY" \
  --argjson context "$CONTEXT" \
  --arg project "$PROJECT" \
  '{
    id: $id,
    timestamp: $timestamp,
    session_id: $session_id,
    type: $type,
    tool: $tool,
    summary: $summary,
    context: $context,
    project: $project,
    tags: []
  }')

OBS_FILE="$OBSERVATIONS_DIR/observations-$TODAY.jsonl"
echo "$OBSERVATION" >> "$OBS_FILE"

exit 0

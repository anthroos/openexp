#!/bin/bash
# OpenExp PostToolUse hook: capture observations from tool usage
# Runs after every tool call — must be fast and never block.
# Only captures Write, Edit, Bash (skips reads).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENEXP_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Directories
OBSERVATIONS_DIR="$HOME/.openexp/observations"
mkdir -p "$OBSERVATIONS_DIR" 2>/dev/null || true

TODAY=$(date +%Y-%m-%d)
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Read hook input from stdin
INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')
CWD=$(echo "$INPUT" | jq -r '.cwd // "/tmp"')
PROJECT=$(basename "$CWD")

# Only capture Edit, Write, Bash
case "$TOOL_NAME" in
  Write|Edit|Bash) ;;
  *) exit 0 ;;
esac

# Extract relevant info based on tool
FILE_PATH=""
COMMAND=""
SUMMARY=""

case "$TOOL_NAME" in
  Write)
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
    [ -z "$FILE_PATH" ] && exit 0
    # Skip sensitive files
    case "$FILE_PATH" in
      *.env|*.env.*|*credentials.json|*token.json|*token_personal.json|*secret*|*.pem|*.key|*/.ssh/*) exit 0 ;;
    esac
    SUMMARY="Wrote $(basename "$FILE_PATH")"
    ;;
  Edit)
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
    [ -z "$FILE_PATH" ] && exit 0
    case "$FILE_PATH" in
      *.env|*.env.*|*credentials.json|*token.json|*token_personal.json|*secret*|*.pem|*.key|*/.ssh/*) exit 0 ;;
    esac
    SUMMARY="Edited $(basename "$FILE_PATH")"
    ;;
  Bash)
    COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
    [ -z "$COMMAND" ] && exit 0
    # Skip trivial read-only commands
    BASE_CMD=$(echo "$COMMAND" | sed 's|^/[^ ]*/||')
    case "$BASE_CMD" in
      ls*|cat\ *|pwd*|echo\ *|head\ *|tail\ *|wc\ *|which\ *|type\ *|cd\ *|test\ *|\[\ *|find\ *) exit 0 ;;
    esac
    # Redact sensitive commands
    if echo "$COMMAND" | grep -qiE '(export.*TOKEN|export.*SECRET|export.*KEY|export.*PASSWORD)'; then
      SUMMARY="Ran: [env variable setup - REDACTED]"
      COMMAND=""
    else
      SUMMARY="Ran: ${COMMAND:0:300}"
    fi
    ;;
esac

[ -z "$SUMMARY" ] && exit 0

# Strip tokens/passwords from summary
SUMMARY=$(echo "$SUMMARY" | sed -E 's/(token|password|api_key|secret|credential)["\047 :=]+[^ "\047]{8,}/\1=[REDACTED]/gi')
SUMMARY=$(echo "$SUMMARY" | sed -E 's/Bearer [A-Za-z0-9_\.\-\/+=]+/Bearer [REDACTED]/g')

# Classify type
TYPE="feature"
if [[ "$FILE_PATH" == *"sales/crm/"* ]]; then TYPE="crm_update"
elif [[ "$FILE_PATH" == *"projects/pm/"* ]]; then TYPE="change"
elif [[ "$FILE_PATH" == *"claude-skills/"* ]]; then TYPE="skill_update"
elif [[ "$FILE_PATH" == *"telegram/"* || "$FILE_PATH" == *"whatsapp/"* ]]; then TYPE="outreach"
elif [[ "$FILE_PATH" == *"docs/"* && "$FILE_PATH" == *".md" ]]; then TYPE="discovery"
elif [[ "$COMMAND" == *"git commit"* || "$COMMAND" == *"gh pr"* ]]; then TYPE="change"
elif [[ "$COMMAND" == *"python"*"send"* || "$COMMAND" == *"node"*"send"* ]]; then TYPE="outreach"
fi

# Generate tags from file path
TAGS="[]"
TAG_LIST=""
[[ "$FILE_PATH" == *"crm"* ]] && TAG_LIST="${TAG_LIST}\"crm\","
[[ "$FILE_PATH" == *"tasks"* || "$FILE_PATH" == *"pm_tasks"* ]] && TAG_LIST="${TAG_LIST}\"tasks\","
[[ "$FILE_PATH" == *"projects"* ]] && TAG_LIST="${TAG_LIST}\"projects\","
[[ "$FILE_PATH" == *"telegram"* ]] && TAG_LIST="${TAG_LIST}\"telegram\","
[[ "$FILE_PATH" == *"email"* ]] && TAG_LIST="${TAG_LIST}\"email\","
[[ "$FILE_PATH" == *"skills"* ]] && TAG_LIST="${TAG_LIST}\"skills\","
[[ "$FILE_PATH" == *"memory"* ]] && TAG_LIST="${TAG_LIST}\"memory\","
[[ "$FILE_PATH" == *"research"* ]] && TAG_LIST="${TAG_LIST}\"research\","
[ -n "$TAG_LIST" ] && TAGS="[${TAG_LIST%,}]"

# Generate observation ID
OBS_ID="obs-$(date +%Y%m%d)-$(openssl rand -hex 4)"

# Build context
if [ -n "$FILE_PATH" ]; then
  CONTEXT=$(jq -n --arg fp "$FILE_PATH" '{"file_path": $fp}')
elif [ -n "$COMMAND" ]; then
  CONTEXT=$(jq -n --arg cmd "${COMMAND:0:300}" '{"command": $cmd}')
else
  CONTEXT="{}"
fi

# Build JSONL observation
OBSERVATION=$(jq -cn \
  --arg id "$OBS_ID" \
  --arg timestamp "$TIMESTAMP" \
  --arg session_id "$SESSION_ID" \
  --arg type "$TYPE" \
  --arg tool "$TOOL_NAME" \
  --arg summary "$SUMMARY" \
  --argjson context "$CONTEXT" \
  --arg project "$PROJECT" \
  --argjson tags "$TAGS" \
  '{
    id: $id,
    timestamp: $timestamp,
    session_id: $session_id,
    type: $type,
    tool: $tool,
    summary: $summary,
    context: $context,
    project: $project,
    tags: $tags
  }')

# Append to today's JSONL file
OBS_FILE="$OBSERVATIONS_DIR/observations-$TODAY.jsonl"
echo "$OBSERVATION" >> "$OBS_FILE"

exit 0

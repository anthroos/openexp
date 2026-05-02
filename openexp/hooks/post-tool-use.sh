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

# redact_secrets: read text on stdin, write redacted text to stdout.
# Uses Python for portable, correct regex (sed character classes diverge
# between BSD and GNU and were broken in earlier versions of this hook).
redact_secrets() {
  python3 -c '
import re, sys
s = sys.stdin.read()

# 1. Inline env-var assignments where the variable name implies a secret.
#    Catches: ANTHROPIC_API_KEY=sk-ant-..., MY_TOKEN=abc, GH_PASSWORD=...
s = re.sub(
    r"(^|\s)([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|API|PASS|PWD|AUTH)[A-Z0-9_]*)\s*=\s*\S+",
    r"\1\2=[REDACTED]",
    s,
)

# 2. keyword=value or keyword: value or keyword="value" forms in prose.
#    Case-insensitive so "API_KEY", "api_key", "Api-Key" all match.
s = re.sub(
    r"(token|password|api[_-]?key|secret|credential|auth)\s*[:=]\s*[\"\x27]?[^\s\"\x27]{4,}[\"\x27]?",
    lambda m: m.group(1) + "=[REDACTED]",
    s,
    flags=re.IGNORECASE,
)

# 3. Bearer / token-prefixed values (sk-ant-..., sk-..., ghp_..., AKIA...).
s = re.sub(r"Bearer\s+[A-Za-z0-9._/+=\-]+", "Bearer [REDACTED]", s)
s = re.sub(r"\bsk-[A-Za-z0-9_\-]{16,}", "[REDACTED]", s)
s = re.sub(r"\bghp_[A-Za-z0-9]{20,}", "[REDACTED]", s)
s = re.sub(r"\bAKIA[A-Z0-9]{16}\b", "[REDACTED]", s)

sys.stdout.write(s)
'
}

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
    # Skip read-only commands. Patterns require either end-of-string or a
    # space after the command name to avoid matching lsof/lsblk/catfish/etc.
    case "$BASE_CMD" in
      ls|ls\ *|cat\ *|pwd|pwd\ *|echo\ *|head\ *|tail\ *|wc\ *|which\ *|type\ *|cd\ *|test\ *|\[\ *|find\ *|grep\ *|rg\ *) exit 0 ;;
    esac
    # Redact the command BEFORE deriving SUMMARY or CONTEXT — both end up on disk.
    COMMAND=$(printf '%s' "$COMMAND" | redact_secrets)
    SUMMARY="Ran: ${COMMAND:0:300}"
    ;;
esac

[ -z "$SUMMARY" ] && exit 0

# Redact summary (covers Wrote/Edited cases plus a defence-in-depth pass on Bash).
SUMMARY=$(printf '%s' "$SUMMARY" | redact_secrets)

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

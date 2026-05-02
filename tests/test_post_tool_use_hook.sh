#!/bin/bash
# Smoke tests for openexp/hooks/post-tool-use.sh
#
# Runs the hook against a set of synthetic Claude Code PostToolUse payloads
# and asserts that:
#   - secret-bearing commands are written to the observation file with
#     [REDACTED] in place of the actual secret
#   - read-only commands (ls, find, cat, …) are skipped (no observation written)
#   - lsof / lsblk / similar non-read-only commands ARE captured (regression
#     test for the earlier `ls*` glob that swallowed them)
#   - sensitive file paths (.env, .ssh/*, *.pem) are skipped on Write/Edit
#
# Usage:  bash tests/test_post_tool_use_hook.sh

set -uo pipefail

HOOK="$(cd "$(dirname "$0")/.." && pwd)/openexp/hooks/post-tool-use.sh"
[ -x "$HOOK" ] || { echo "Hook not executable: $HOOK"; exit 2; }

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
OBS_FILE="$TMPDIR/observations-$(date +%Y-%m-%d).jsonl"
touch "$OBS_FILE"

PASS=0
FAIL=0

run_hook() {
  # $1 = command string. Builds the JSON payload and pipes into the hook.
  jq -n --arg cmd "$1" \
    '{tool_name:"Bash",session_id:"t",cwd:"/tmp/proj",tool_input:{command:$cmd}}' \
    | OPENEXP_OBSERVATIONS_DIR="$TMPDIR" bash "$HOOK"
}

run_write_hook() {
  jq -n --arg fp "$1" \
    '{tool_name:"Write",session_id:"t",cwd:"/tmp/proj",tool_input:{file_path:$fp}}' \
    | OPENEXP_OBSERVATIONS_DIR="$TMPDIR" bash "$HOOK"
}

last_summary() { tail -1 "$OBS_FILE" | jq -r '.summary // empty'; }
file_lines()   { wc -l < "$OBS_FILE" | tr -d ' '; }

assert_redacted() {
  local label="$1" cmd="$2" forbidden="$3"
  run_hook "$cmd"
  local s
  s=$(last_summary)
  if echo "$s" | grep -q "REDACTED" && ! echo "$s" | grep -q "$forbidden"; then
    PASS=$((PASS+1)); printf "  PASS  %s\n" "$label"
  else
    FAIL=$((FAIL+1)); printf "  FAIL  %s\n        summary: %s\n" "$label" "$s"
  fi
}

assert_skipped() {
  local label="$1" cmd="$2"
  local before=$(file_lines)
  run_hook "$cmd"
  local after=$(file_lines)
  if [ "$before" = "$after" ]; then
    PASS=$((PASS+1)); printf "  PASS  %s\n" "$label"
  else
    FAIL=$((FAIL+1)); printf "  FAIL  %s (was captured but should skip)\n" "$label"
  fi
}

assert_captured() {
  local label="$1" cmd="$2"
  local before=$(file_lines)
  run_hook "$cmd"
  local after=$(file_lines)
  if [ "$after" != "$before" ]; then
    PASS=$((PASS+1)); printf "  PASS  %s\n" "$label"
  else
    FAIL=$((FAIL+1)); printf "  FAIL  %s (was skipped but should capture)\n" "$label"
  fi
}

assert_write_skipped() {
  local label="$1" path="$2"
  local before=$(file_lines)
  run_write_hook "$path"
  local after=$(file_lines)
  if [ "$before" = "$after" ]; then
    PASS=$((PASS+1)); printf "  PASS  %s\n" "$label"
  else
    FAIL=$((FAIL+1)); printf "  FAIL  %s (was captured but should skip)\n" "$label"
  fi
}

echo "=== Secret redaction (Bash) ==="
assert_redacted "api_key= form"        'curl -d api_key="abc12345xyz_secretvalue" url'           "abc12345xyz_secretvalue"
assert_redacted "Bearer sk-ant-..."    'curl -H "Authorization: Bearer sk-ant-api03-realsecret" url'  "sk-ant-api03-realsecret"
assert_redacted "--token=val"          'npm publish --token=secretvalue1234567890'               "secretvalue1234567890"
assert_redacted "--password=\"val\""   'mysql -u root --password="hunter2hunter2"'               "hunter2hunter2"
assert_redacted "inline ENV=val"       'ANTHROPIC_API_KEY=sk-ant-api03-realsecret npm test'      "sk-ant-api03-realsecret"
assert_redacted "export GITHUB_TOKEN"  'export GITHUB_TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

echo "=== Read-only commands skipped ==="
assert_skipped "ls -la"                'ls -la'
assert_skipped "find / -name x"        'find / -name x'
assert_skipped "cat /etc/hosts"        'cat /etc/hosts'
assert_skipped "pwd"                   'pwd'
assert_skipped "echo hi"               'echo hi'
assert_skipped "head -1 file"          'head -1 file'

echo "=== Non-read-only commands captured (regression: ls* glob) ==="
assert_captured "lsof -i :8080"        'lsof -i :8080'
assert_captured "lsblk"                'lsblk'

echo "=== Sensitive file paths skipped (Write) ==="
assert_write_skipped ".env file"       '/home/u/.env'
assert_write_skipped ".env.local"      '/home/u/.env.local'
assert_write_skipped "credentials.json" '/home/u/credentials.json'
assert_write_skipped ".ssh/id_rsa"     '/home/u/.ssh/id_rsa'
assert_write_skipped "*.pem"           '/home/u/cert.pem'

echo ""
echo "Result: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]

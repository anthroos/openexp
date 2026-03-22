#!/bin/bash
# OpenExp Setup — Q-learning memory for Claude Code
# Usage: ./setup.sh
#
# This script:
# 1. Creates Python venv + installs deps
# 2. Starts Qdrant (Docker) if not running
# 3. Creates Qdrant collection
# 4. Copies .env.example → .env
# 5. Registers MCP server in Claude Code settings
# 6. Registers hooks in Claude Code settings
# 7. Verifies everything works
set -euo pipefail

OPENEXP_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_SETTINGS="$HOME/.claude/settings.local.json"
COLLECTION="openexp_memories"
EMBEDDING_DIM=384

echo "🧠 OpenExp Setup — Q-learning memory for Claude Code"
echo "=================================================="
echo ""

# --- Step 1: Check prerequisites ---
echo "Step 1/7: Checking prerequisites..."

# Python 3.11+
if ! command -v python3 &>/dev/null; then
  echo "❌ Python 3 not found. Install Python 3.11+ first."
  exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  echo "❌ Python $PY_VERSION found, but 3.11+ is required."
  exit 1
fi
echo "  ✅ Python $PY_VERSION"

# Docker
if ! command -v docker &>/dev/null; then
  echo "❌ Docker not found. Install Docker Desktop first."
  echo "  → https://www.docker.com/products/docker-desktop/"
  exit 1
fi
echo "  ✅ Docker"

# jq
if ! command -v jq &>/dev/null; then
  echo "❌ jq not found. Install with: brew install jq"
  exit 1
fi
echo "  ✅ jq"

# Claude Code
if ! command -v claude &>/dev/null; then
  echo "⚠️  Claude Code CLI not found. Install it first:"
  echo "  → npm install -g @anthropic-ai/claude-code"
  echo "  Continuing anyway (you can install it later)..."
fi

echo ""

# --- Step 2: Create venv + install deps ---
echo "Step 2/7: Setting up Python environment..."
if [ ! -d "$OPENEXP_DIR/.venv" ]; then
  python3 -m venv "$OPENEXP_DIR/.venv"
fi
"$OPENEXP_DIR/.venv/bin/pip" install -q --upgrade pip
"$OPENEXP_DIR/.venv/bin/pip" install -q -e "$OPENEXP_DIR"
echo "  ✅ Dependencies installed"
echo ""

# --- Step 3: Start Qdrant ---
echo "Step 3/7: Starting Qdrant..."
if docker ps --format '{{.Names}}' | grep -q '^openexp-qdrant$'; then
  echo "  ✅ Qdrant already running"
else
  if docker ps -a --format '{{.Names}}' | grep -q '^openexp-qdrant$'; then
    docker start openexp-qdrant >/dev/null
  else
    DOCKER_ARGS=(-d --name openexp-qdrant --restart unless-stopped
      -p 127.0.0.1:6333:6333
      --user 1000:1000
      -v openexp_qdrant_data:/qdrant/storage)
    if [ -n "${QDRANT_API_KEY:-}" ]; then
      DOCKER_ARGS+=(-e "QDRANT__SERVICE__API_KEY=$QDRANT_API_KEY")
    fi
    docker run "${DOCKER_ARGS[@]}" qdrant/qdrant:latest >/dev/null
  fi
  # Wait for Qdrant to be ready
  echo -n "  Waiting for Qdrant..."
  QDRANT_READY=0
  for i in $(seq 1 30); do
    if curl -sf http://localhost:6333/healthz >/dev/null 2>&1; then
      echo " ready!"
      QDRANT_READY=1
      break
    fi
    echo -n "."
    sleep 1
  done
  if [ "$QDRANT_READY" -eq 0 ]; then
    echo ""
    echo "  ❌ Qdrant failed to start within 30 seconds."
    echo "  Check: docker logs openexp-qdrant"
    exit 1
  fi
fi
echo ""

# --- Step 4: Create collection ---
echo "Step 4/7: Creating Qdrant collection..."
COLLECTION_EXISTS=$(curl -sf "http://localhost:6333/collections/$COLLECTION" 2>/dev/null | jq -r '.status // "not_found"')
if [ "$COLLECTION_EXISTS" = "ok" ]; then
  echo "  ✅ Collection '$COLLECTION' already exists"
else
  curl -sf -X PUT "http://localhost:6333/collections/$COLLECTION" \
    -H 'Content-Type: application/json' \
    -d "{
      \"vectors\": {
        \"size\": $EMBEDDING_DIM,
        \"distance\": \"Cosine\"
      }
    }" >/dev/null
  echo "  ✅ Collection '$COLLECTION' created"
fi
echo ""

# --- Step 5: Create .env ---
echo "Step 5/7: Setting up configuration..."
if [ ! -f "$OPENEXP_DIR/.env" ]; then
  cp "$OPENEXP_DIR/.env.example" "$OPENEXP_DIR/.env"
  echo "  ✅ Created .env from template"
else
  echo "  ✅ .env already exists"
fi
echo ""

# --- Step 6: Register MCP server + hooks ---
echo "Step 6/7: Registering with Claude Code..."

# Create settings file if it doesn't exist
if [ ! -f "$CLAUDE_SETTINGS" ]; then
  mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
  echo '{}' > "$CLAUDE_SETTINGS"
fi

# Read current settings
SETTINGS=$(cat "$CLAUDE_SETTINGS")

# Add MCP server
SETTINGS=$(echo "$SETTINGS" | jq --arg dir "$OPENEXP_DIR" '
  .mcpServers.openexp = {
    "command": ($dir + "/.venv/bin/python3"),
    "args": ["-m", "openexp.mcp_server"],
    "cwd": $dir
  }
')

# Add hooks
HOOKS_DIR="$OPENEXP_DIR/openexp/hooks"
SETTINGS=$(echo "$SETTINGS" | jq --arg hooks_dir "$HOOKS_DIR" '
  # SessionStart hook
  .hooks.SessionStart = (.hooks.SessionStart // []) |
  if any(.[]; .command | contains("openexp")) then . else
    . + [{"type": "command", "command": ($hooks_dir + "/session-start.sh")}]
  end |

  # UserPromptSubmit hook
  .hooks.UserPromptSubmit = (.hooks.UserPromptSubmit // []) |
  if any(.[]; .command | contains("openexp")) then . else
    . + [{"type": "command", "command": ($hooks_dir + "/user-prompt-recall.sh")}]
  end |

  # PostToolUse hook
  .hooks.PostToolUse = (.hooks.PostToolUse // []) |
  if any(.[]; .command | contains("openexp")) then . else
    . + [{"type": "command", "command": ($hooks_dir + "/post-tool-use.sh")}]
  end |

  # SessionEnd hook
  .hooks.SessionEnd = (.hooks.SessionEnd // []) |
  if any(.[]; .command | contains("openexp")) then . else
    . + [{"type": "command", "command": ($hooks_dir + "/session-end.sh"), "timeout": 30}]
  end
')

echo "$SETTINGS" | jq '.' > "$CLAUDE_SETTINGS"
echo "  ✅ MCP server registered"
echo "  ✅ Hooks registered (SessionStart, UserPromptSubmit, PostToolUse, SessionEnd)"
echo ""

# --- Step 7: Verify ---
echo "Step 7/7: Verifying installation..."

# Test Python imports
if "$OPENEXP_DIR/.venv/bin/python3" -c "from openexp.core.q_value import QCache; print('  ✅ Python imports OK')" 2>/dev/null; then
  true
else
  echo "  ❌ Python imports failed"
  exit 1
fi

# Test Qdrant connection
if "$OPENEXP_DIR/.venv/bin/python3" -c "
import os
from qdrant_client import QdrantClient
api_key = os.environ.get('QDRANT_API_KEY', '').strip() or None
qc = QdrantClient(host='localhost', port=6333, api_key=api_key)
info = qc.get_collection('$COLLECTION')
print(f'  ✅ Qdrant OK (collection: $COLLECTION, vectors: {info.points_count})')
" 2>/dev/null; then
  true
else
  echo "  ❌ Qdrant connection failed"
  exit 1
fi

echo ""
echo "=================================================="
echo "🎉 OpenExp is ready!"
echo ""
echo "What happens now:"
echo "  1. Open Claude Code in any project: claude"
echo "  2. SessionStart hook will inject relevant memories"
echo "  3. PostToolUse hook captures your work as observations"
echo "  4. MCP tools (search_memory, add_memory) are available"
echo "  5. Q-values improve automatically based on session outcomes"
echo ""
echo "To ingest observations into Qdrant:"
echo "  $OPENEXP_DIR/.venv/bin/python3 -m openexp.cli ingest"
echo ""
echo "To search memories:"
echo "  $OPENEXP_DIR/.venv/bin/python3 -m openexp.cli search -q 'your query'"
echo ""
echo "To check stats:"
echo "  $OPENEXP_DIR/.venv/bin/python3 -m openexp.cli stats"
echo "=================================================="

"""OpenExp MCP Server — exposes Q-learning memory to Claude Code via STDIO.

SECURITY: This server MUST only run over STDIO transport (stdin/stdout).
If HTTP transport is ever added, authentication (e.g., bearer tokens, mTLS)
MUST be implemented before exposing the server on any network interface.
Running over HTTP without authentication would allow unauthenticated access
to the memory store and Q-value system.
"""
import atexit
import json
import sys
import logging
import uuid

logger = logging.getLogger(__name__)

# Lazy-initialized globals (set in _init_server)
q_cache = None
q_updater = None
reward_tracker = None
direct_search = None
SESSION_ID = None
DELTAS_DIR = None
Q_CACHE_PATH = None
_initialized = False


def _init_server():
    """Initialize server state. Called once from main(), not at import time."""
    global q_cache, q_updater, reward_tracker, direct_search
    global SESSION_ID, DELTAS_DIR, Q_CACHE_PATH, _initialized

    if _initialized:
        return

    from .core.config import DATA_DIR, Q_CACHE_PATH as _qcp
    from .core.q_value import QCache, QValueUpdater
    from .core import direct_search as _ds
    from .reward_tracker import RewardTracker

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Q_CACHE_PATH = _qcp
    direct_search = _ds
    SESSION_ID = uuid.uuid4().hex[:12]
    DELTAS_DIR = DATA_DIR / "deltas"

    q_cache = QCache()
    q_cache.load_and_merge(Q_CACHE_PATH, DELTAS_DIR)

    q_updater = QValueUpdater(cache=q_cache)
    reward_tracker = RewardTracker(data_dir=DATA_DIR, q_updater=q_updater, q_cache=q_cache)

    atexit.register(lambda: q_cache.save_delta(DELTAS_DIR, SESSION_ID))
    _initialized = True


TOOLS = [
    {
        "name": "search_memory",
        "description": "Search memories with FastEmbed + Qdrant, hybrid BM25 scoring, lifecycle filtering, and Q-value reranking",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "agent": {"type": "string", "description": "Filter by agent name"},
                "type": {"type": "string", "description": "Filter by memory type"},
                "client_id": {"type": "string", "description": "Filter by client ID"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_memory",
        "description": "Store a new memory with FastEmbed embedding and Q-value tracking",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "agent": {"type": "string", "default": "main"},
                "type": {"type": "string", "default": "fact"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "log_prediction",
        "description": "Log an agent prediction for tracking and Q-value learning. Returns prediction_id for later resolution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prediction": {"type": "string", "description": "What the agent predicts will happen"},
                "confidence": {"type": "number", "default": 0.5, "description": "Agent confidence [0, 1]"},
                "strategic_value": {"type": "number", "default": 0.5, "description": "How important [0, 1]"},
                "memory_ids_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "description": "Memory IDs that were retrieved for this prediction",
                },
                "client_id": {"type": "string", "description": "Associated client ID"},
            },
            "required": ["prediction"],
        },
    },
    {
        "name": "log_outcome",
        "description": "Resolve a prediction with outcome and reward. Updates Q-values for all memories used.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prediction_id": {"type": "string", "description": "ID from log_prediction"},
                "outcome": {"type": "string", "description": "What actually happened"},
                "reward": {"type": "number", "description": "Reward signal [-1.0, 1.0]"},
                "cause_category": {
                    "type": "string",
                    "description": "Why strategy succeeded/failed: execution_failure, strategy_failure, qualification_failure, hypothesis_failure, external, competition",
                },
            },
            "required": ["prediction_id", "outcome", "reward"],
        },
    },
    {
        "name": "get_agent_context",
        "description": "Get full context for agent decision-making: memories + Q-scores + pending predictions",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for relevant memories"},
                "client_id": {"type": "string", "description": "Client ID for filtering"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "reflect",
        "description": "Trigger reflection on recent memories to find patterns and insights",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "default": 24, "description": "Hours to look back"},
            },
            "required": [],
        },
    },
    {
        "name": "memory_stats",
        "description": "Get memory system statistics including Q-cache and prediction counts",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "reload_q_cache",
        "description": "Reload Q-cache from disk. Use after manual calibration or bulk Q-value updates.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


MAX_CONTENT_LENGTH = 10000
MAX_SEARCH_LIMIT = 100
MAX_REFLECT_HOURS = 720  # 30 days


def _clamp(value, lo, hi):
    """Clamp a numeric value to [lo, hi]."""
    return max(lo, min(hi, value))


class _ErrorResponse(Exception):
    """Raised by handle_request to signal a JSONRPC error (not result)."""
    def __init__(self, code, message):
        self.code = code
        self.message = message


def handle_request(request: dict) -> dict:
    """Handle a single MCP JSON-RPC request."""
    method = request.get("method")

    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "openexp", "version": "0.1.0"},
        }

    elif method == "notifications/initialized":
        return None  # notification — no response

    elif method == "tools/list":
        return {"tools": TOOLS}

    elif method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if not tool_name:
            raise _ErrorResponse(-32602, "Missing tool name")

        if tool_name == "search_memory":
            result = direct_search.search_memories(
                query=args["query"][:MAX_CONTENT_LENGTH],
                limit=_clamp(args.get("limit", 10), 1, MAX_SEARCH_LIMIT),
                agent_id=args.get("agent"),
                memory_type=args.get("type"),
                client_id=args.get("client_id"),
                q_cache=q_cache,
            )
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

        elif tool_name == "add_memory":
            content = args["content"]
            if len(content) > MAX_CONTENT_LENGTH:
                return {"content": [{"type": "text", "text": json.dumps({"error": f"Content too long ({len(content)} chars, max {MAX_CONTENT_LENGTH})"})}]}
            result = direct_search.add_memory(
                content=content,
                agent_id=args.get("agent", "main"),
                memory_type=args.get("type", "fact"),
                metadata={"source": "mcp"},
                q_cache=q_cache,
            )
            return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

        elif tool_name == "log_prediction":
            pred_id = reward_tracker.log_prediction(
                prediction=args["prediction"][:MAX_CONTENT_LENGTH],
                confidence=_clamp(args.get("confidence", 0.5), 0.0, 1.0),
                strategic_value=_clamp(args.get("strategic_value", 0.5), 0.0, 1.0),
                memory_ids_used=args.get("memory_ids_used", []),
                client_id=args.get("client_id"),
            )
            return {"content": [{"type": "text", "text": json.dumps({"prediction_id": pred_id})}]}

        elif tool_name == "log_outcome":
            result = reward_tracker.log_outcome(
                prediction_id=args["prediction_id"],
                outcome=args["outcome"][:MAX_CONTENT_LENGTH],
                reward=_clamp(args["reward"], -1.0, 1.0),
                cause_category=args.get("cause_category"),
            )
            q_cache.save_delta(DELTAS_DIR, SESSION_ID)
            return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

        elif tool_name == "get_agent_context":
            search_result = direct_search.search_memories(
                query=args["query"][:MAX_CONTENT_LENGTH],
                limit=_clamp(args.get("limit", 10), 1, MAX_SEARCH_LIMIT),
                client_id=args.get("client_id"),
                q_cache=q_cache,
            )
            memories = search_result.get("results", [])

            pending = reward_tracker.get_pending_predictions(
                client_id=args.get("client_id")
            )

            result = {
                "query": args["query"],
                "memories": memories,
                "memory_count": len(memories),
                "pending_predictions": pending,
            }
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

        elif tool_name == "reflect":
            hours = _clamp(args.get("hours", 24), 1, MAX_REFLECT_HOURS)
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            search_result = direct_search.search_memories(
                query="recent patterns decisions insights",
                limit=20,
                q_cache=q_cache,
            )
            # Filter to memories within the time window
            all_results = search_result.get("results", [])
            filtered = []
            for r in all_results:
                created = r.get("created_at", "")
                if created and created >= cutoff.isoformat():
                    filtered.append(r)
                elif not created:
                    filtered.append(r)  # include if no timestamp

            result = {
                "status": "reflected",
                "hours": hours,
                "memories_found": len(filtered),
                "top_memories": [
                    {
                        "content": r.get("memory", "")[:200],
                        "q_value": r.get("q_value", 0.5),
                        "type": r.get("memory_type", "fact"),
                    }
                    for r in filtered[:10]
                ],
            }
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

        elif tool_name == "reload_q_cache":
            old_size = len(q_cache)
            q_cache.load_and_merge(Q_CACHE_PATH, DELTAS_DIR)
            new_size = len(q_cache)
            result = {"status": "reloaded", "old_size": old_size, "new_size": new_size}
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        elif tool_name == "memory_stats":
            stats = {
                "q_cache_size": len(q_cache),
                "pending_predictions": len(reward_tracker.get_pending_predictions()),
                "reward_stats": reward_tracker.get_prediction_stats(),
            }
            return {"content": [{"type": "text", "text": json.dumps(stats, indent=2, default=str)}]}

        raise _ErrorResponse(-32601, f"Unknown tool: {tool_name}")

    raise _ErrorResponse(-32601, f"Unknown method: {method}")


def main():
    """Run MCP STDIO transport."""
    _init_server()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            result = handle_request(request)

            # JSON-RPC notifications (no id) get no response
            if "id" not in request:
                continue

            if result is None:
                continue

            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            print(json.dumps(response, default=str), flush=True)
        except json.JSONDecodeError:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: invalid JSON"},
            }
            print(json.dumps(error_response), flush=True)
        except _ErrorResponse as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": e.code, "message": e.message},
            }
            print(json.dumps(error_response), flush=True)
        except Exception as e:
            logger.exception("MCP request failed: %s", e)
            error_response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "Internal error"},
            }
            print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    main()

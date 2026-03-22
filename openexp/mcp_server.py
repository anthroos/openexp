"""OpenExp MCP Server — exposes Q-learning memory to Claude Code via STDIO."""
import atexit
import json
import sys
import logging
import uuid

from .core.config import DATA_DIR, Q_CACHE_PATH
from .core.q_value import QCache, QValueUpdater
from .core import direct_search
from .reward_tracker import RewardTracker

logger = logging.getLogger(__name__)

# Unique session ID per MCP process (for delta files)
SESSION_ID = uuid.uuid4().hex[:12]
DELTAS_DIR = DATA_DIR / "deltas"

# Init Q-cache: load main + merge any pending deltas
q_cache = QCache()
q_cache.load_and_merge(Q_CACHE_PATH, DELTAS_DIR)

q_updater = QValueUpdater(cache=q_cache)
reward_tracker = RewardTracker(data_dir=DATA_DIR, q_updater=q_updater, q_cache=q_cache)

# Save only this session's changes as delta on shutdown
atexit.register(lambda: q_cache.save_delta(DELTAS_DIR, SESSION_ID))


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


def handle_request(request: dict) -> dict:
    """Handle a single MCP JSON-RPC request."""
    method = request.get("method")

    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "openexp", "version": "0.1.0"},
        }

    elif method == "tools/list":
        return {"tools": TOOLS}

    elif method == "tools/call":
        tool_name = request["params"]["name"]
        args = request["params"].get("arguments", {})

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
            # Lightweight reflection: search recent memories and summarize
            search_result = direct_search.search_memories(
                query="recent patterns decisions insights",
                limit=20,
                q_cache=q_cache,
            )
            result = {
                "status": "reflected",
                "memories_found": len(search_result.get("results", [])),
                "top_memories": [
                    {
                        "content": r.get("memory", "")[:200],
                        "q_value": r.get("q_value", 0.5),
                        "type": r.get("memory_type", "fact"),
                    }
                    for r in search_result.get("results", [])[:10]
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

        return {"error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}

    return {"error": {"code": -32601, "message": f"Unknown method: {method}"}}


def main():
    """Run MCP STDIO transport."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            result = handle_request(request)
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            print(json.dumps(response, default=str), flush=True)
        except json.JSONDecodeError as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
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

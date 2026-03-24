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
active_experience = None
SESSION_ID = None
DELTAS_DIR = None
Q_CACHE_PATH = None
_initialized = False


def _init_server():
    """Initialize server state. Called once from main(), not at import time."""
    global q_cache, q_updater, reward_tracker, direct_search, active_experience
    global SESSION_ID, DELTAS_DIR, Q_CACHE_PATH, _initialized

    if _initialized:
        return

    from .core.config import DATA_DIR, Q_CACHE_PATH as _qcp
    from .core.q_value import QCache, QValueUpdater
    from .core import direct_search as _ds
    from .core.experience import get_active_experience
    from .reward_tracker import RewardTracker

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Q_CACHE_PATH = _qcp
    direct_search = _ds
    SESSION_ID = uuid.uuid4().hex[:12]
    DELTAS_DIR = DATA_DIR / "deltas"

    active_experience = get_active_experience()
    logger.info("Active experience: %s", active_experience.name)

    q_cache = QCache()
    q_cache.load_and_merge(Q_CACHE_PATH, DELTAS_DIR)

    q_updater = QValueUpdater(cache=q_cache)
    reward_tracker = RewardTracker(
        data_dir=DATA_DIR,
        q_updater=q_updater,
        q_cache=q_cache,
        experience=active_experience.name,
    )

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
                "client_id": {"type": "string", "description": "Associated client/entity ID"},
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
        "name": "resolve_outcomes",
        "description": "Run outcome resolvers to detect business events (CRM stage changes) and apply rewards to tagged memories",
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
    # Phase 2: Introspection tools
    {
        "name": "experience_info",
        "description": "Get current active experience config (name, weights, resolvers, boosts)",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "experience_top_memories",
        "description": "Get top or bottom N memories by Q-value in the active experience",
        "inputSchema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "default": 10, "description": "Number of memories to return"},
                "bottom": {"type": "boolean", "default": False, "description": "If true, return lowest Q-value memories instead"},
            },
            "required": [],
        },
    },
    {
        "name": "experience_insights",
        "description": "Get reward distribution, learning velocity, and most/least valuable memory types in the active experience",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "calibrate_experience_q",
        "description": "Manually set Q-value for a memory in the active experience",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Memory ID to calibrate"},
                "q_value": {"type": "number", "description": "New Q-value [-0.5, 1.0]"},
            },
            "required": ["memory_id", "q_value"],
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
    exp_name = active_experience.name if active_experience else "default"

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
                experience=exp_name,
            )
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

        elif tool_name == "add_memory":
            content = args["content"]
            if len(content) > MAX_CONTENT_LENGTH:
                return {"content": [{"type": "text", "text": json.dumps({"error": f"Content too long ({len(content)} chars, max {MAX_CONTENT_LENGTH})"})}]}
            meta = {"source": "mcp"}
            if args.get("client_id"):
                meta["client_id"] = args["client_id"]
            result = direct_search.add_memory(
                content=content,
                agent_id=args.get("agent", "main"),
                memory_type=args.get("type", "fact"),
                metadata=meta,
                q_cache=q_cache,
                experience=exp_name,
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
                experience=exp_name,
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
                "experience": exp_name,
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
                experience=exp_name,
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
                "experience": exp_name,
                "memories_found": len(filtered),
                "top_memories": [
                    {
                        "content": r.get("memory", "")[:200],
                        "q_value": r.get("q_value", 0.0),
                        "type": r.get("memory_type", "fact"),
                    }
                    for r in filtered[:10]
                ],
            }
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

        elif tool_name == "resolve_outcomes":
            from .ingest import _load_configured_resolvers
            from .outcome import resolve_outcomes

            resolvers = _load_configured_resolvers()
            if not resolvers:
                return {"content": [{"type": "text", "text": json.dumps({"status": "no_resolvers", "message": "No outcome resolvers configured"})}]}

            result = resolve_outcomes(
                resolvers=resolvers,
                reward_tracker=reward_tracker,
                q_cache=q_cache,
                q_updater=q_updater,
                experience=exp_name,
            )

            if result.get("total_events", 0) > 0:
                q_cache.save_delta(DELTAS_DIR, SESSION_ID)

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
                "active_experience": exp_name,
                "experience_stats": q_cache.get_experience_stats(exp_name),
                "pending_predictions": len(reward_tracker.get_pending_predictions()),
                "reward_stats": reward_tracker.get_prediction_stats(),
            }
            return {"content": [{"type": "text", "text": json.dumps(stats, indent=2, default=str)}]}

        # Phase 2: Introspection tools
        elif tool_name == "experience_info":
            info = {
                "name": active_experience.name,
                "description": active_experience.description,
                "session_reward_weights": active_experience.session_reward_weights,
                "outcome_resolvers": active_experience.outcome_resolvers,
                "retrieval_boosts": active_experience.retrieval_boosts,
                "q_config_overrides": active_experience.q_config_overrides,
                "stats": q_cache.get_experience_stats(exp_name),
            }
            return {"content": [{"type": "text", "text": json.dumps(info, indent=2, default=str)}]}

        elif tool_name == "experience_top_memories":
            n = _clamp(args.get("n", 10), 1, 100)
            bottom = args.get("bottom", False)

            # Collect all memories with Q-data for this experience
            entries = []
            for mem_id, exp_dict in q_cache._cache.items():
                q_data = exp_dict.get(exp_name)
                if q_data:
                    entries.append({
                        "memory_id": mem_id,
                        "q_value": q_data.get("q_value", 0.0),
                        "q_visits": q_data.get("q_visits", 0),
                        "last_reward": q_data.get("last_reward"),
                    })

            entries.sort(key=lambda x: x["q_value"], reverse=not bottom)
            result = {
                "experience": exp_name,
                "direction": "bottom" if bottom else "top",
                "count": len(entries[:n]),
                "memories": entries[:n],
            }
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

        elif tool_name == "experience_insights":
            from collections import Counter

            q_values = []
            visits = []
            rewards = []
            for exp_dict in q_cache._cache.values():
                q_data = exp_dict.get(exp_name)
                if q_data:
                    q_values.append(q_data.get("q_value", 0.0))
                    visits.append(q_data.get("q_visits", 0))
                    last_r = q_data.get("last_reward")
                    if last_r is not None:
                        rewards.append(last_r)

            # Distribution buckets
            buckets = Counter()
            for q in q_values:
                if q < -0.25:
                    buckets["very_negative"] += 1
                elif q < 0:
                    buckets["negative"] += 1
                elif q < 0.25:
                    buckets["neutral"] += 1
                elif q < 0.5:
                    buckets["positive"] += 1
                else:
                    buckets["very_positive"] += 1

            result = {
                "experience": exp_name,
                "total_memories": len(q_values),
                "q_distribution": dict(buckets),
                "q_mean": round(sum(q_values) / len(q_values), 4) if q_values else 0,
                "q_min": round(min(q_values), 4) if q_values else 0,
                "q_max": round(max(q_values), 4) if q_values else 0,
                "avg_visits": round(sum(visits) / len(visits), 2) if visits else 0,
                "avg_last_reward": round(sum(rewards) / len(rewards), 4) if rewards else 0,
                "memories_never_visited": sum(1 for v in visits if v == 0),
            }
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

        elif tool_name == "calibrate_experience_q":
            mem_id = args["memory_id"]
            new_q = _clamp(args["q_value"], -0.5, 1.0)

            q_data = q_cache.get(mem_id, exp_name) or {
                "q_action": 0.0,
                "q_hypothesis": 0.0,
                "q_fit": 0.0,
                "q_visits": 0,
            }
            q_data["q_value"] = new_q
            q_data["q_action"] = new_q
            q_data["q_hypothesis"] = new_q
            q_data["q_fit"] = new_q
            from datetime import datetime, timezone
            q_data["q_updated_at"] = datetime.now(timezone.utc).isoformat()
            q_cache.set(mem_id, q_data, exp_name)

            result = {
                "memory_id": mem_id,
                "experience": exp_name,
                "new_q_value": new_q,
                "status": "calibrated",
            }
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

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

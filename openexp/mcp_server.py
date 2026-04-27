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
                "role": {"type": "string", "description": "Filter by role: user or assistant"},
                "session_id": {"type": "string", "description": "Filter by session ID"},
                "source": {"type": "string", "description": "Filter by source: transcript, decision, etc."},
                "date_from": {"type": "string", "format": "date", "description": "Start date (ISO format, e.g. 2026-04-01)"},
                "date_to": {"type": "string", "format": "date", "description": "End date (ISO format, e.g. 2026-04-08)"},
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
        "description": (
            "Log a pack-grounded prediction. REQUIRED whenever the assistant cites "
            "a specific relative_day of an installed experience pack as the basis "
            "for a real-world action recommendation. Captures: which step was cited, "
            "which case it applies to, what was recommended (and what was explicitly "
            "NOT recommended), the observable signal that resolves the prediction, "
            "and the window in days. Returns prediction_id for later log_outcome."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack_id": {"type": "string", "description": "The pack's slug (e.g. 'inbound-acquisition-with-free-pilot')"},
                "pack_author": {"type": "string", "description": "The pack's author handle (e.g. 'ivan-pasichnyk')"},
                "cited_step": {"type": "string", "description": "The exact relative_day cited (e.g. 'day +57')"},
                "case_id": {"type": "string", "description": "External reference for this case (CRM lead_id, ticket ID, etc.) — opaque string"},
                "applied_action": {"type": "string", "description": "What the assistant recommended TO do, derived from the cited step"},
                "prevented_action": {"type": "string", "description": "What the assistant recommended NOT to do (negative-space prediction). Optional but encouraged — often the higher-value half."},
                "expected_signal": {"type": "string", "description": "Observable signal that resolves this prediction (e.g. 'counterparty signs both sides')"},
                "expected_window_days": {"type": "integer", "description": "Deadline in days for log_outcome to be called"},
                "notes": {"type": "string", "description": "Optional free-text context"},

                "prediction": {"type": "string", "description": "[deprecated] Free-text prediction. Use applied_action + expected_signal instead. Accepted for backward compat."},
                "confidence": {"type": "number", "description": "[deprecated, removed from required schema 2026-04-27 — Claude confidence is uncalibrated until ≥30 outcome datapoints. Accepted for backward compat.]"},
                "strategic_value": {"type": "number", "description": "[deprecated, accepted for backward compat]"},
                "memory_ids_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "description": "Memory IDs that were retrieved for this prediction (for legacy Q-value updates on log_outcome)",
                },
                "client_id": {"type": "string", "description": "[deprecated alias for case_id, accepted for backward compat]"},
            },
            # Required only on the new path. If `prediction` is provided we treat the
            # call as legacy and skip the new-path required check (handled in dispatcher).
            "required": [],
        },
    },
    {
        "name": "log_outcome",
        "description": (
            "Resolve a prediction with observed facts. New path: provide actual_signal "
            "and days_to_resolve — interpretation-free record of what happened. Legacy "
            "path: provide outcome + reward to keep updating Q-values for memory_ids_used "
            "from older predictions. The two paths can coexist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prediction_id": {"type": "string", "description": "ID from log_prediction"},
                "actual_signal": {"type": "string", "description": "What was observed — raw fact, no interpretation. Required on the new path."},
                "days_to_resolve": {"type": "integer", "description": "How many days from prediction to resolution. Required on the new path."},
                "notes": {"type": "string", "description": "Optional free-text context (e.g. unexpected events)"},

                "outcome": {"type": "string", "description": "[deprecated alias for actual_signal — accepted for backward compat]"},
                "reward": {"type": "number", "description": "[deprecated — only used on the legacy Q-update path. Omit on the new path.]"},
                "cause_category": {
                    "type": "string",
                    "description": "[deprecated, accepted for backward compat]",
                },
            },
            "required": ["prediction_id"],
        },
    },
    {
        "name": "memory_stats",
        "description": "Get memory system health: point counts by source/role, pending predictions, date range, Q-cache size",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


MAX_CONTENT_LENGTH = 10000
MAX_SEARCH_LIMIT = 100


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
                role=args.get("role"),
                session_id=args.get("session_id"),
                source=args.get("source"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
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
            # New-path required fields (pack-grounded prediction).
            new_path_fields = (
                "pack_id", "pack_author", "cited_step", "case_id",
                "applied_action", "expected_signal", "expected_window_days",
            )
            has_new_path = all(f in args for f in new_path_fields)
            has_legacy = "prediction" in args
            if not has_new_path and not has_legacy:
                missing = [f for f in new_path_fields if f not in args]
                raise _ErrorResponse(
                    -32602,
                    "Missing required fields. Provide either the new-path fields "
                    f"({', '.join(new_path_fields)}) or the legacy `prediction` field. "
                    f"Missing on new path: {missing}",
                )

            pred_id = reward_tracker.log_prediction(
                # New-path
                pack_id=args.get("pack_id"),
                pack_author=args.get("pack_author"),
                cited_step=args.get("cited_step"),
                case_id=args.get("case_id") or args.get("client_id"),
                applied_action=args.get("applied_action"),
                prevented_action=args.get("prevented_action"),
                expected_signal=args.get("expected_signal"),
                expected_window_days=args.get("expected_window_days"),
                notes=args.get("notes"),
                # Legacy
                prediction=(args.get("prediction") or "")[:MAX_CONTENT_LENGTH] or None,
                confidence=(
                    _clamp(args["confidence"], 0.0, 1.0) if "confidence" in args else None
                ),
                strategic_value=(
                    _clamp(args["strategic_value"], 0.0, 1.0) if "strategic_value" in args else None
                ),
                memory_ids_used=args.get("memory_ids_used", []),
                client_id=args.get("client_id"),
            )
            return {"content": [{"type": "text", "text": json.dumps({"prediction_id": pred_id})}]}

        elif tool_name == "log_outcome":
            if "prediction_id" not in args:
                raise _ErrorResponse(-32602, "Missing required field: prediction_id")

            # New path: actual_signal + days_to_resolve. Legacy: outcome + reward.
            actual_signal = args.get("actual_signal") or args.get("outcome")
            if not actual_signal:
                raise _ErrorResponse(
                    -32602,
                    "Provide either `actual_signal` (new path) or `outcome` (legacy).",
                )

            has_legacy_reward = "reward" in args

            result = reward_tracker.log_outcome(
                prediction_id=args["prediction_id"],
                actual_signal=actual_signal[:MAX_CONTENT_LENGTH],
                days_to_resolve=args.get("days_to_resolve"),
                notes=args.get("notes"),
                # Legacy Q-update path
                reward=_clamp(args["reward"], -1.0, 1.0) if has_legacy_reward else None,
                cause_category=args.get("cause_category"),
            )
            if has_legacy_reward:
                q_cache.save_delta(DELTAS_DIR, SESSION_ID)
            return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

        elif tool_name == "memory_stats":
            from .core.config import COLLECTION_NAME
            try:
                from qdrant_client import QdrantClient
                qclient = QdrantClient(url="http://localhost:6333", timeout=5)
                collection_info = qclient.get_collection(COLLECTION_NAME)
                total_points = collection_info.points_count

                # Count by source
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                by_source = {}
                for src in ["transcript", "decision", "mcp"]:
                    cnt = qclient.count(
                        collection_name=COLLECTION_NAME,
                        count_filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value=src))]),
                        exact=True,
                    )
                    if cnt.count > 0:
                        by_source[src] = cnt.count

                # Count by role
                by_role = {}
                for role in ["user", "assistant"]:
                    cnt = qclient.count(
                        collection_name=COLLECTION_NAME,
                        count_filter=Filter(must=[FieldCondition(key="role", match=MatchValue(value=role))]),
                        exact=True,
                    )
                    if cnt.count > 0:
                        by_role[role] = cnt.count

                # Experience labels count
                exp_cnt = qclient.count(
                    collection_name=COLLECTION_NAME,
                    count_filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value="experience_library"))]),
                    exact=True,
                )
                if exp_cnt.count > 0:
                    by_source["experience_library"] = exp_cnt.count

                qdrant_stats = {
                    "total_points": total_points,
                    "by_source": by_source,
                    "by_role": by_role,
                    "status": "ok",
                }
            except Exception as e:
                logger.exception("Qdrant stats failed: %s", e)
                qdrant_stats = {"status": "error", "error": "Qdrant unavailable"}

            stats = {
                "qdrant": qdrant_stats,
                "q_cache_size": len(q_cache),
                "active_experience": exp_name,
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

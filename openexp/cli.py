"""OpenExp CLI — quick search for hooks and scripts.

Lightweight CLI designed for SessionStart hooks (10s timeout).

Usage:
    python3 -m openexp.cli search -q "project context" -n 3
    python3 -m openexp.cli ingest --dry-run
    python3 -m openexp.cli stats
"""
import argparse
import json
import sys
import logging

logging.basicConfig(level=logging.WARNING)


def cmd_search(args):
    """Search memories via direct Qdrant + FastEmbed."""
    from .core.config import Q_CACHE_PATH
    from .core.q_value import QCache
    from .core import direct_search

    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)

    results = direct_search.search_memories(
        query=args.query,
        limit=args.limit,
        memory_type=getattr(args, "type", None),
        exclude_type=getattr(args, "exclude_type", None),
        q_cache=q_cache,
    )

    if args.format == "text":
        for r in results.get("results", []):
            score = r.get("hybrid_score", r.get("score", 0))
            q_val = r.get("q_value", 0.5)
            content = r.get("memory", r.get("content", ""))[:200]
            print(f"[sim={score:.2f} q={q_val:.2f}] {content}")
    else:
        print(json.dumps(results, indent=2, default=str))


def cmd_ingest(args):
    """Ingest observations and session summaries into Qdrant."""
    if not args.dry_run:
        logging.getLogger("openexp.ingest").setLevel(logging.INFO)

    from .ingest import ingest_session

    result = ingest_session(
        max_count=args.max,
        dry_run=args.dry_run,
        sessions_only=args.sessions_only,
        session_id=args.session_id,
    )

    print(json.dumps(result, indent=2, default=str))

    obs = result.get("observations", {})
    sess = result.get("sessions", {})
    if args.dry_run:
        print(f"\n[dry-run] Would ingest: {obs.get('would_ingest', 0)} observations, "
              f"{sess.get('would_ingest', 0)} sessions")
    else:
        print(f"\nIngested: {obs.get('ingested', 0)} observations, "
              f"{sess.get('ingested', 0)} sessions")


def cmd_log_retrieval(args):
    """Log which memories were retrieved at session start."""
    from .ingest.retrieval_log import log_retrieval

    memory_ids = [mid for mid in args.memory_ids.split(",") if mid]
    scores = [float(s) for s in args.scores.split(",") if s] if args.scores else []

    if not memory_ids:
        return

    log_retrieval(
        session_id=args.session_id,
        query=args.query or "",
        memory_ids=memory_ids,
        scores=scores,
    )


def cmd_stats(args):
    """Show memory system stats."""
    from .core.config import Q_CACHE_PATH
    from .core.q_value import QCache

    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)

    print(f"Q-cache entries: {len(q_cache._cache)}")
    if q_cache._cache:
        q_values = [v.get("q_value", 0.5) for v in q_cache._cache.values()]
        print(f"Q-value range: [{min(q_values):.3f}, {max(q_values):.3f}]")
        print(f"Q-value mean:  {sum(q_values)/len(q_values):.3f}")


def main():
    parser = argparse.ArgumentParser(
        prog="openexp",
        description="OpenExp CLI — Q-value weighted memory search",
    )
    sub = parser.add_subparsers(dest="cmd")

    # search
    sp_search = sub.add_parser("search", help="Search memories")
    sp_search.add_argument("--query", "-q", required=True, help="Search query")
    sp_search.add_argument("--limit", "-n", type=int, default=5, help="Max results")
    sp_search.add_argument("--type", "-t", default=None, help="Filter by memory type")
    sp_search.add_argument("--exclude-type", default=None, help="Exclude memory type")
    sp_search.add_argument(
        "--format", "-f", choices=["json", "text"], default="text", help="Output format"
    )

    # ingest
    sp_ingest = sub.add_parser("ingest", help="Ingest observations into Qdrant")
    sp_ingest.add_argument("--dry-run", action="store_true", help="Preview without writing")
    sp_ingest.add_argument("--max", type=int, default=0, help="Max observations to ingest (0=all)")
    sp_ingest.add_argument("--sessions-only", action="store_true", help="Only ingest session summaries")
    sp_ingest.add_argument("--session-id", default=None, help="Session ID for retrieval reward")

    # log-retrieval
    sp_log = sub.add_parser("log-retrieval", help="Log retrieved memory IDs for a session")
    sp_log.add_argument("--session-id", required=True, help="Session ID")
    sp_log.add_argument("--query", default="", help="Search query used")
    sp_log.add_argument("--memory-ids", required=True, help="Comma-separated memory IDs")
    sp_log.add_argument("--scores", default="", help="Comma-separated scores")

    # stats
    sub.add_parser("stats", help="Show memory stats")

    args = parser.parse_args()

    if args.cmd == "search":
        cmd_search(args)
    elif args.cmd == "ingest":
        cmd_ingest(args)
    elif args.cmd == "log-retrieval":
        cmd_log_retrieval(args)
    elif args.cmd == "stats":
        cmd_stats(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

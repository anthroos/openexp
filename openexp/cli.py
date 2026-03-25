"""OpenExp CLI — quick search for hooks and scripts.

Lightweight CLI designed for SessionStart hooks (10s timeout).

Usage:
    python3 -m openexp.cli search -q "project context" -n 3
    python3 -m openexp.cli ingest --dry-run
    python3 -m openexp.cli stats
    python3 -m openexp.cli experience list
    python3 -m openexp.cli experience show sales
    python3 -m openexp.cli experience stats
    python3 -m openexp.cli experience create
    python3 -m openexp.cli compact --dry-run
"""
import argparse
import json
import sys
import logging

logging.basicConfig(level=logging.WARNING)


MAX_QUERY_LENGTH = 2000
MAX_MEMORY_IDS = 100


def _get_experience_name(args) -> str:
    """Get experience name from args or env."""
    if hasattr(args, "experience") and args.experience:
        return args.experience
    from .core.config import ACTIVE_EXPERIENCE
    return ACTIVE_EXPERIENCE


def cmd_search(args):
    """Search memories via direct Qdrant + FastEmbed."""
    if len(args.query) > MAX_QUERY_LENGTH:
        print(f"Error: query too long ({len(args.query)} chars, max {MAX_QUERY_LENGTH})", file=sys.stderr)
        sys.exit(1)

    from .core.config import Q_CACHE_PATH
    from .core.q_value import QCache
    from .core import direct_search

    experience = _get_experience_name(args)

    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)

    results = direct_search.search_memories(
        query=args.query,
        limit=args.limit,
        memory_type=getattr(args, "type", None),
        exclude_type=getattr(args, "exclude_type", None),
        q_cache=q_cache,
        experience=experience,
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

    if len(memory_ids) > MAX_MEMORY_IDS:
        print(f"Error: too many memory IDs ({len(memory_ids)}, max {MAX_MEMORY_IDS})", file=sys.stderr)
        sys.exit(1)

    log_retrieval(
        session_id=args.session_id,
        query=args.query or "",
        memory_ids=memory_ids,
        scores=scores,
    )


def cmd_resolve(args):
    """Run outcome resolvers to detect CRM changes and apply rewards."""
    logging.getLogger("openexp").setLevel(logging.INFO)

    from .core.config import Q_CACHE_PATH
    from .core.q_value import QCache, QValueUpdater
    from .ingest import _load_configured_resolvers
    from .outcome import resolve_outcomes

    experience = _get_experience_name(args)

    resolvers = _load_configured_resolvers()
    if not resolvers:
        print("No outcome resolvers configured. Set OPENEXP_OUTCOME_RESOLVERS in .env")
        sys.exit(1)

    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)
    q_updater = QValueUpdater(cache=q_cache)

    result = resolve_outcomes(
        resolvers=resolvers,
        q_cache=q_cache,
        q_updater=q_updater,
        experience=experience,
    )

    if result.get("total_events", 0) > 0:
        q_cache.save(Q_CACHE_PATH)

    print(json.dumps(result, indent=2, default=str))

    events = result.get("total_events", 0)
    rewarded = result.get("memories_rewarded", 0)
    resolved = result.get("predictions_resolved", 0)
    print(f"\nOutcomes: {events} events, {rewarded} memories rewarded, {resolved} predictions resolved")


def cmd_viz(args):
    """Generate interactive visualization dashboard or session replay."""
    import webbrowser
    from pathlib import Path

    from .viz import export_viz_data, export_replay_data, find_best_replay_session, generate_demo_replay

    output = Path(args.output)

    # Demo mode
    if getattr(args, 'demo', False):
        print("Generating demo replay...")
        data = generate_demo_replay()

        template_path = Path(__file__).parent / "static" / "replay.html"
        template = template_path.read_text()

        data_script = f"<script>const REPLAY_DATA = {json.dumps(data, default=str)};</script>"
        html = template.replace("<!-- DATA_PLACEHOLDER -->", data_script)

        if args.output == "./openexp-viz.html":
            output = Path("./openexp-replay-demo.html")

        output.write_text(html)
        size_kb = output.stat().st_size / 1024
        print(f"Written: {output} (self-contained, {size_kb:.0f} KB)")

        if not args.no_open:
            print("Opening in browser...")
            webbrowser.open(f"file://{output.resolve()}")
        return

    # Replay mode
    if args.replay:
        session_id = args.replay
        if session_id == "latest":
            print("Finding best session for replay...")
            session_id = find_best_replay_session()
            if not session_id:
                print("No suitable sessions found.", file=sys.stderr)
                sys.exit(1)
            print(f"  Selected: {session_id[:8]}")

        print(f"Exporting replay for session {session_id[:8]}...")
        data = export_replay_data(session_id)

        if "error" in data:
            print(f"Error: {data['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"  Steps: {data['meta']['total_steps']}")
        print(f"  Observations: {data['meta']['total_observations']}")
        print(f"  Memories: {data['meta']['memories_retrieved']}")

        template_path = Path(__file__).parent / "static" / "replay.html"
        template = template_path.read_text()

        data_script = f"<script>const REPLAY_DATA = {json.dumps(data, default=str)};</script>"
        html = template.replace("<!-- DATA_PLACEHOLDER -->", data_script)

        # Default output name for replay (only if user didn't specify --output)
        if args.output == "./openexp-viz.html":
            output = Path(f"./openexp-replay-{data['meta']['session_id']}.html")

        output.write_text(html)
        size_kb = output.stat().st_size / 1024
        print(f"Written: {output} (self-contained, {size_kb:.0f} KB)")

        if not args.no_open:
            print("Opening in browser...")
            webbrowser.open(f"file://{output.resolve()}")
        return

    # Dashboard mode
    print("Exporting visualization data...")
    data = export_viz_data(no_qdrant=args.no_qdrant)

    print(f"  Q-cache: {data['meta']['total_memories']:,} entries")
    print(f"  Observations: {len(data['observations_timeline'])} daily files")
    print(f"  Sessions: {data['meta']['total_sessions']} tracked")

    template_path = Path(__file__).parent / "static" / "viz.html"
    template = template_path.read_text()

    data_script = f"<script>const VIZ_DATA = {json.dumps(data, default=str)};</script>"
    html = template.replace("<!-- DATA_PLACEHOLDER -->", data_script)

    output.write_text(html)
    size_kb = output.stat().st_size / 1024
    print(f"Written: {output} (self-contained, {size_kb:.0f} KB)")

    if not args.no_open:
        print("Opening in browser...")
        webbrowser.open(f"file://{output.resolve()}")


def cmd_stats(args):
    """Show memory system stats."""
    from .core.config import Q_CACHE_PATH
    from .core.q_value import QCache

    experience = _get_experience_name(args)

    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)

    print(f"Q-cache entries: {len(q_cache._cache)}")
    print(f"Active experience: {experience}")

    stats = q_cache.get_experience_stats(experience)
    if stats["count"] > 0:
        print(f"Experience '{experience}': {stats['count']} memories with Q-data")
        print(f"  Q-value range: [{stats['min']:.3f}, {stats['max']:.3f}]")
        print(f"  Q-value mean:  {stats['mean']:.3f}")
    else:
        print(f"Experience '{experience}': no Q-data yet")

    # Show other experiences if any
    all_exps = set()
    for exp_dict in q_cache._cache.values():
        all_exps.update(exp_dict.keys())
    if len(all_exps) > 1:
        print(f"\nAll experiences in cache: {', '.join(sorted(all_exps))}")


def _rating_to_weight(rating: int) -> float:
    """Convert 0-10 rating to 0.0-0.30 weight."""
    table = {10: 0.30, 9: 0.28, 8: 0.25, 7: 0.20, 6: 0.15, 5: 0.12,
             4: 0.10, 3: 0.07, 2: 0.05, 1: 0.02, 0: 0.0}
    return table.get(rating, 0.0)


def _ask_int(prompt: str, low: int, high: int, default: int | None = None) -> int:
    """Ask for an integer in [low, high] range."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt} ({low}-{high}){suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            val = int(raw)
            if low <= val <= high:
                return val
        except ValueError:
            pass
        print(f"  Please enter a number between {low} and {high}.")


def _ask_choice(prompt: str, choices: list[tuple[str, str]], default: int = 1) -> int:
    """Ask user to pick from numbered choices. Returns 0-based index."""
    print(f"\n{prompt}")
    for i, (label, desc) in enumerate(choices, 1):
        marker = " (default)" if i == default else ""
        print(f"  {i}. {label} — {desc}{marker}")
    while True:
        raw = input(f"Choice [1-{len(choices)}, default={default}]: ").strip()
        if not raw:
            return default - 1
        try:
            val = int(raw)
            if 1 <= val <= len(choices):
                return val - 1
        except ValueError:
            pass
        print(f"  Please enter 1-{len(choices)}.")


def _experience_create_wizard():
    """Interactive wizard to create a custom experience YAML."""
    import yaml
    from .core.config import EXPERIENCES_DIR

    print("=" * 50)
    print("  OpenExp — Create Custom Experience")
    print("=" * 50)

    # Name
    while True:
        name = input("\nExperience name (lowercase, no spaces): ").strip().lower().replace(" ", "-")
        if name and name.isidentifier() or all(c.isalnum() or c == "-" for c in name):
            break
        print("  Use only letters, numbers, and hyphens.")

    # Description
    desc = input("One-line description: ").strip() or f"{name} experience"

    # Signal ratings
    signals = [
        ("commit", "Committed code to git"),
        ("pr", "Created a Pull Request"),
        ("pr_merged", "PR merged"),
        ("writes", "Edited/created files"),
        ("deploy", "Deployed to production"),
        ("release", "Published a release/tag"),
        ("tests", "Tests passed"),
        ("review_approved", "Code review approved"),
        ("ticket_closed", "Ticket/issue closed"),
        ("decisions", "Recorded a decision"),
        ("email_sent", "Sent an email"),
        ("telegram_sent", "Sent Telegram message"),
        ("slack_sent", "Sent Slack message"),
        ("follow_up", "Made a follow-up"),
        ("proposal_sent", "Sent a proposal"),
        ("invoice_sent", "Sent an invoice"),
        ("call_scheduled", "Scheduled a call"),
        ("nda_exchanged", "Exchanged NDA/agreement"),
        ("payment_received", "Payment received"),
    ]

    print("\n--- Rate each signal 0-10 (how important for YOUR workflow) ---")
    print("  10 = this IS the goal   5 = moderate   0 = irrelevant")
    print()

    weights = {}
    for key, label in signals:
        rating = _ask_int(f"  {label}", 0, 10, default=0)
        w = _rating_to_weight(rating)
        if key == "writes":
            w = round(w / 5, 3)  # per-file weight, cap at ~0.06/file
        weights[key] = w

    # Penalties
    penalty_idx = _ask_choice(
        "How strict should penalties be?",
        [
            ("Lenient", "research/exploration sessions are normal (base: -0.03)"),
            ("Moderate", "most sessions should produce something (base: -0.05)"),
            ("Strict", "no output = wasted time (base: -0.10)"),
        ],
        default=2,
    )
    base_penalties = [
        {"base": -0.03, "min_obs_penalty": -0.02, "no_output_penalty": -0.03},
        {"base": -0.05, "min_obs_penalty": -0.03, "no_output_penalty": -0.05},
        {"base": -0.10, "min_obs_penalty": -0.05, "no_output_penalty": -0.10},
    ]
    weights.update(base_penalties[penalty_idx])

    # Learning speed
    alpha_idx = _ask_choice(
        "How fast does your domain change?",
        [
            ("Fast", "sales, news — learn fast, forget fast (α=0.30)"),
            ("Normal", "engineering — balanced (α=0.25)"),
            ("Slow", "research, legal — accumulate gradually (α=0.15)"),
        ],
        default=2,
    )
    alpha_values = [0.30, 0.25, 0.15]
    alpha = alpha_values[alpha_idx]

    # Retrieval boosts
    print("\n--- Which memory types should rank higher in search? ---")
    boosts = {}
    boost_types = [
        ("decision", "Strategic choices"),
        ("outcome", "Results of past actions"),
        ("fact", "Domain knowledge"),
    ]
    for mem_type, label in boost_types:
        boost_idx = _ask_choice(
            f"Boost for '{mem_type}' ({label})?",
            [
                ("None", "no boost (1.0×)"),
                ("Mild", "slight boost (1.1×)"),
                ("Strong", "significant boost (1.3×)"),
            ],
            default=1,
        )
        boost_val = [1.0, 1.1, 1.3][boost_idx]
        if boost_val > 1.0:
            boosts[mem_type] = boost_val

    # Outcome resolvers
    use_crm = _ask_choice(
        "Do you use CRM-based outcome tracking?",
        [
            ("No", "no external outcome resolvers"),
            ("Yes", "enable CRM CSV resolver (requires OPENEXP_CRM_DIR)"),
        ],
        default=1,
    )
    resolvers = ["openexp.resolvers.crm_csv:CRMCSVResolver"] if use_crm == 1 else []

    # Build YAML
    experience = {
        "name": name,
        "description": desc,
        "session_reward_weights": weights,
        "outcome_resolvers": resolvers,
        "retrieval_boosts": boosts if boosts else {},
        "q_config_overrides": {"alpha": alpha} if alpha != 0.25 else {},
    }

    # Summary
    total_positive = sum(v for v in weights.values() if v > 0)
    print("\n" + "=" * 50)
    print(f"  Experience: {name}")
    print(f"  Description: {desc}")
    print(f"  Total positive weight: {total_positive:.2f}")
    if total_positive < 0.5:
        print("  ⚠ Low total — sessions may rarely earn positive reward")
    elif total_positive > 1.5:
        print("  ⚠ High total — most sessions will max out reward")
    print(f"  Alpha: {alpha}")
    print("=" * 50)

    yaml_text = yaml.dump(experience, default_flow_style=False, sort_keys=False)
    print(f"\n{yaml_text}")

    # Save
    EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EXPERIENCES_DIR / f"{name}.yaml"

    confirm = input(f"Save to {out_path}? [Y/n]: ").strip().lower()
    if confirm in ("", "y", "yes"):
        out_path.write_text(yaml_text)
        print(f"\nSaved: {out_path}")
        print(f"Activate: export OPENEXP_EXPERIENCE={name}")
    else:
        print("Not saved. You can copy the YAML above manually.")


def cmd_compact(args):
    """Run memory compaction — merge similar memories into compressed entries."""
    logging.getLogger("openexp").setLevel(logging.INFO)

    from .core.compaction import compact_memories

    experience = _get_experience_name(args)

    result = compact_memories(
        max_distance=args.max_distance,
        min_cluster_size=args.min_cluster,
        client_id=getattr(args, "client_id", None),
        project=getattr(args, "project", None),
        experience=experience,
        dry_run=args.dry_run,
        max_clusters=args.max_clusters,
    )

    if args.dry_run:
        print(f"\n[dry-run] Found {result['memories_found']} active memories")
        print(f"[dry-run] {result['clusters']} clusters found")
        for detail in result.get("details", []):
            print(f"  Cluster ({detail['original_count']} memories, Q={detail['q_value']:.3f}, "
                  f"kappa={detail['kappa']:.1f}):")
            preview = detail["merged_content"][:100]
            print(f"    {preview}...")
    else:
        print(f"\nCompacted: {result.get('compacted', 0)} clusters "
              f"({result.get('memories_merged', 0)} memories merged)")

    print(json.dumps(result, indent=2, default=str))


def cmd_experience(args):
    """Manage experiences."""
    from .core.experience import load_experience, list_experiences

    subcmd = args.experience_cmd

    if subcmd == "list":
        exps = list_experiences()
        for exp in exps:
            print(f"  {exp.name}: {exp.description}")

    elif subcmd == "show":
        name = args.name if hasattr(args, "name") and args.name else "default"
        exp = load_experience(name)
        info = {
            "name": exp.name,
            "description": exp.description,
            "session_reward_weights": exp.session_reward_weights,
            "outcome_resolvers": exp.outcome_resolvers,
            "retrieval_boosts": exp.retrieval_boosts,
            "q_config_overrides": exp.q_config_overrides,
        }
        print(json.dumps(info, indent=2))

    elif subcmd == "create":
        _experience_create_wizard()

    elif subcmd == "stats":
        from .core.config import Q_CACHE_PATH
        from .core.q_value import QCache

        q_cache = QCache()
        q_cache.load(Q_CACHE_PATH)

        # Collect all experiences
        all_exps = set()
        for exp_dict in q_cache._cache.values():
            all_exps.update(exp_dict.keys())

        if not all_exps:
            print("No experience data in Q-cache yet.")
            return

        for exp_name in sorted(all_exps):
            stats = q_cache.get_experience_stats(exp_name)
            print(f"{exp_name}: {stats['count']} memories, "
                  f"Q mean={stats['mean']:.3f}, "
                  f"range=[{stats['min']:.3f}, {stats['max']:.3f}]")
    else:
        print("Usage: openexp experience {list|show|stats}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="openexp",
        description="OpenExp CLI — Q-value weighted memory search",
    )
    parser.add_argument(
        "--experience", "-e",
        default=None,
        help="Experience name (overrides OPENEXP_EXPERIENCE env var)",
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

    # resolve
    sub.add_parser("resolve", help="Run outcome resolvers (CRM stage changes → rewards)")

    # stats
    sub.add_parser("stats", help="Show memory stats")

    # experience
    sp_exp = sub.add_parser("experience", help="Manage experiences")
    sp_exp.add_argument("experience_cmd", choices=["list", "show", "stats", "create"], help="Subcommand")
    sp_exp.add_argument("name", nargs="?", default=None, help="Experience name (for show/create)")

    # compact
    sp_compact = sub.add_parser("compact", help="Merge similar memories into compressed entries")
    sp_compact.add_argument("--dry-run", action="store_true", help="Preview clusters without merging")
    sp_compact.add_argument("--max-distance", type=float, default=0.25, help="Max cosine distance for clustering (0.0-1.0)")
    sp_compact.add_argument("--min-cluster", type=int, default=3, help="Minimum cluster size to compact")
    sp_compact.add_argument("--max-clusters", type=int, default=50, help="Max clusters to process")
    sp_compact.add_argument("--client-id", default=None, help="Filter by client ID")
    sp_compact.add_argument("--project", default=None, help="Filter by project name")

    # viz
    sp_viz = sub.add_parser("viz", help="Generate interactive visualization dashboard")
    sp_viz.add_argument("--output", "-o", default="./openexp-viz.html", help="Output HTML path")
    sp_viz.add_argument("--no-open", action="store_true", help="Don't open browser")
    sp_viz.add_argument("--no-qdrant", action="store_true", help="Skip Qdrant queries")
    sp_viz.add_argument("--replay", default=None, help="Session ID for replay mode (or 'latest')")
    sp_viz.add_argument("--demo", action="store_true", help="Generate scripted demo replay")

    args = parser.parse_args()

    if args.cmd == "search":
        cmd_search(args)
    elif args.cmd == "ingest":
        cmd_ingest(args)
    elif args.cmd == "log-retrieval":
        cmd_log_retrieval(args)
    elif args.cmd == "resolve":
        cmd_resolve(args)
    elif args.cmd == "stats":
        cmd_stats(args)
    elif args.cmd == "compact":
        cmd_compact(args)
    elif args.cmd == "experience":
        cmd_experience(args)
    elif args.cmd == "viz":
        cmd_viz(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

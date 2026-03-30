"""OpenExp Visualization — data export for self-contained HTML dashboard.

Reads Q-cache, observations, sessions, predictions/outcomes and produces
a sanitized JSON dict that gets embedded in the viz.html template.

No raw memory text or file paths are included — aggregate stats only.
"""
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def _histogram(values, bin_start=-0.5, bin_end=1.0, num_bins=15):
    """Create histogram bins from a list of numeric values."""
    if not values:
        return {"histogram": [], "stats": {}}

    step = (bin_end - bin_start) / num_bins
    counts = [0] * num_bins
    for v in values:
        idx = int((v - bin_start) / step)
        idx = max(0, min(idx, num_bins - 1))
        counts[idx] += 1

    bins = []
    for i in range(num_bins):
        lo = bin_start + i * step
        hi = lo + step
        bins.append({"bin_start": round(lo, 4), "bin_end": round(hi, 4), "count": counts[i]})

    return {
        "histogram": bins,
        "stats": {
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "mean": round(statistics.mean(values), 4),
            "median": round(statistics.median(values), 4),
            "std": round(statistics.stdev(values), 4) if len(values) > 1 else 0,
            "count": len(values),
        },
    }


def _parse_date(ts_str):
    """Extract date string (YYYY-MM-DD) from an ISO timestamp."""
    if not ts_str:
        return None
    return ts_str[:10]


def _load_jsonl(path):
    """Load JSONL file, return list of dicts. Silently skip bad lines."""
    entries = []
    p = Path(path)
    if not p.exists():
        return entries
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def _count_lines(path):
    """Count lines in a file without reading content."""
    p = Path(path)
    if not p.exists():
        return 0
    count = 0
    with open(p, "rb") as f:
        for _ in f:
            count += 1
    return count


def export_viz_data(no_qdrant=False):
    """Export all visualization data as a dict ready for JSON embedding.

    Args:
        no_qdrant: Skip Qdrant queries (lifecycle stats, memory types).
                   Useful when Docker is not running.

    Returns:
        dict with all visualization data (sanitized, no raw text/paths).
    """
    from .core.config import (
        DATA_DIR, Q_CACHE_PATH, OBSERVATIONS_DIR, SESSIONS_DIR,
    )
    from .core.q_value import QCache, DEFAULT_Q_CONFIG
    from .core.hybrid_search import DEFAULT_HYBRID_WEIGHTS, STATUS_WEIGHTS

    data = {}

    # --- Q-cache ---
    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)
    cache = q_cache._cache

    # Extract flat q_data for default experience from nested format
    def _flat(exp_dict):
        """Get q_data for 'default' experience from nested cache entry."""
        if isinstance(exp_dict, dict) and "default" in exp_dict:
            return exp_dict["default"]
        return exp_dict  # fallback for any legacy format

    flat_values = [_flat(v) for v in cache.values()]

    q_combined = [v.get("q_value", 0.0) for v in flat_values]
    q_action = [v.get("q_action", 0.0) for v in flat_values]
    q_hypothesis = [v.get("q_hypothesis", 0.5) for v in flat_values]
    q_fit = [v.get("q_fit", 0.5) for v in flat_values]

    data["q_distribution"] = {
        "combined": _histogram(q_combined),
        "action": _histogram(q_action),
        "hypothesis": _histogram(q_hypothesis),
        "fit": _histogram(q_fit),
    }

    # Q-value evolution over time (group by date)
    date_groups = defaultdict(lambda: {"combined": [], "action": [], "hypothesis": [], "fit": []})
    for v in flat_values:
        date = _parse_date(v.get("q_updated_at", ""))
        if date:
            date_groups[date]["combined"].append(v.get("q_value", 0.0))
            date_groups[date]["action"].append(v.get("q_action", 0.0))
            date_groups[date]["hypothesis"].append(v.get("q_hypothesis", 0.5))
            date_groups[date]["fit"].append(v.get("q_fit", 0.5))

    q_evolution = []
    for date in sorted(date_groups.keys()):
        g = date_groups[date]
        q_evolution.append({
            "date": date,
            "mean_combined": round(statistics.mean(g["combined"]), 4) if g["combined"] else 0,
            "mean_action": round(statistics.mean(g["action"]), 4) if g["action"] else 0,
            "mean_hypothesis": round(statistics.mean(g["hypothesis"]), 4) if g["hypothesis"] else 0,
            "mean_fit": round(statistics.mean(g["fit"]), 4) if g["fit"] else 0,
            "count_updated": len(g["combined"]),
        })
    data["q_evolution"] = q_evolution

    # Visits distribution
    visits = [v.get("q_visits", 0) for v in flat_values]
    visit_counts = Counter(visits)
    data["visits_distribution"] = {
        "histogram": [
            {"visits": k, "count": v}
            for k, v in sorted(visit_counts.items())
        ]
    }

    # Calibration counts
    calibrations = Counter(v.get("calibration", "uncalibrated") or "uncalibrated" for v in flat_values)
    data["calibration_counts"] = dict(calibrations)

    # --- Scoring config ---
    data["scoring_config"] = {
        "weights": {k: round(v, 2) for k, v in DEFAULT_HYBRID_WEIGHTS.items()},
        "q_layer_weights": {
            "action": DEFAULT_Q_CONFIG["q_action_weight"],
            "hypothesis": DEFAULT_Q_CONFIG["q_hypothesis_weight"],
            "fit": DEFAULT_Q_CONFIG["q_fit_weight"],
        },
        "q_learning": {
            "alpha": DEFAULT_Q_CONFIG["alpha"],
            "q_init": DEFAULT_Q_CONFIG["q_init"],
            "q_floor": DEFAULT_Q_CONFIG["q_floor"],
            "q_ceiling": DEFAULT_Q_CONFIG["q_ceiling"],
        },
        "status_weights": {k: round(v, 2) for k, v in STATUS_WEIGHTS.items()},
    }

    # --- Observations (line counts only, no content) ---
    obs_dir = Path(OBSERVATIONS_DIR)
    obs_timeline = []
    if obs_dir.exists():
        for f in sorted(obs_dir.glob("observations-*.jsonl")):
            # Extract date from filename: observations-YYYY-MM-DD.jsonl
            m = re.search(r"observations-(\d{4}-\d{2}-\d{2})\.jsonl$", f.name)
            if m:
                obs_timeline.append({
                    "date": m.group(1),
                    "observations_count": _count_lines(f),
                })
    data["observations_timeline"] = obs_timeline

    # --- Sessions ---
    sessions_dir = Path(SESSIONS_DIR)
    session_dates = Counter()
    if sessions_dir.exists():
        for f in sessions_dir.glob("*.md"):
            # Filename: YYYY-MM-DD-hexid.md
            m = re.search(r"^(\d{4}-\d{2}-\d{2})", f.name)
            if m:
                session_dates[m.group(1)] += 1
    data["sessions_by_date"] = [
        {"date": d, "count": c} for d, c in sorted(session_dates.items())
    ]

    # --- Session retrievals ---
    retrievals_path = DATA_DIR / "session_retrievals.jsonl"
    retrievals = _load_jsonl(retrievals_path)
    retrieval_dates = Counter()
    retrieval_scores = []
    for r in retrievals:
        date = _parse_date(r.get("timestamp", ""))
        if date:
            retrieval_dates[date] += 1
        scores = r.get("scores", [])
        retrieval_scores.extend(scores)

    data["retrievals"] = {
        "total": len(retrievals),
        "by_date": [{"date": d, "count": c} for d, c in sorted(retrieval_dates.items())],
        "score_stats": _histogram(retrieval_scores, bin_start=0, bin_end=1.0, num_bins=10) if retrieval_scores else {"histogram": [], "stats": {}},
    }

    # --- Predictions & outcomes ---
    predictions = _load_jsonl(DATA_DIR / "predictions.jsonl")
    outcomes = _load_jsonl(DATA_DIR / "outcomes.jsonl")

    resolved_count = sum(1 for p in predictions if p.get("status") == "resolved")
    pending_count = sum(1 for p in predictions if p.get("status") != "resolved")
    outcome_rewards = [o.get("reward", 0) for o in outcomes]

    data["predictions"] = {
        "total": len(predictions),
        "resolved": resolved_count,
        "pending": pending_count,
        "avg_reward": round(statistics.mean(outcome_rewards), 4) if outcome_rewards else 0,
        "reward_distribution": _histogram(outcome_rewards, bin_start=-1.0, bin_end=1.0, num_bins=10) if outcome_rewards else {"histogram": [], "stats": {}},
    }

    # --- Lifecycle (Qdrant) ---
    lifecycle_data = {}
    memory_types = {}
    if not no_qdrant:
        try:
            from .core.lifecycle import MemoryLifecycle
            lc = MemoryLifecycle()
            lifecycle_data = lc.get_lifecycle_stats()
        except Exception:
            lifecycle_data = {}

        try:
            from .core.config import COLLECTION_NAME, QDRANT_HOST, QDRANT_PORT
            from qdrant_client import QdrantClient
            client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=5)
            # Get memory type distribution
            scroll_result = client.scroll(
                collection_name=COLLECTION_NAME,
                limit=100,
                with_payload=["type"],
            )
            type_counts = Counter()
            # Scroll all points to count types
            points, next_offset = scroll_result
            while points:
                for point in points:
                    t = (point.payload or {}).get("type", "unknown")
                    type_counts[t] += 1
                if next_offset is None:
                    break
                points, next_offset = client.scroll(
                    collection_name=COLLECTION_NAME,
                    offset=next_offset,
                    limit=100,
                    with_payload=["type"],
                )
            memory_types = dict(type_counts)
        except Exception:
            memory_types = {}

    data["lifecycle"] = lifecycle_data
    data["memory_types"] = memory_types

    # --- Meta ---
    all_dates = [_parse_date(v.get("q_updated_at", "")) for v in cache.values()]
    all_dates = [d for d in all_dates if d]

    data["meta"] = {
        "generated_at": datetime.now().isoformat(),
        "total_memories": len(cache),
        "total_observations": sum(o["observations_count"] for o in obs_timeline),
        "total_sessions": sum(s["count"] for s in data["sessions_by_date"]),
        "total_retrievals": len(retrievals),
        "data_range": {
            "first": min(all_dates) if all_dates else None,
            "last": max(all_dates) if all_dates else None,
        },
    }

    _sanitize(data)
    return data


def _redact(text):
    """Redact sensitive info from observation summaries for demo display."""
    if not text:
        return ""
    # Redact file paths (with or without trailing path)
    text = re.sub(r"/Users/\w+(?:/[^\s\"']*)?", "/~/...", text)
    text = re.sub(r"/home/\w+(?:/[^\s\"']*)?", "/~/...", text)
    # Redact email addresses → keep domain hint
    text = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", lambda m: m.group(0).split("@")[0][:2] + "***@" + m.group(0).split("@")[1], text)
    # Redact API keys
    text = re.sub(r"sk-ant-\S+", "sk-***", text)
    return text


def _classify_step(obs):
    """Classify an observation into a human-readable step type for the replay."""
    tool = obs.get("tool", "")
    summary = obs.get("summary", "")
    s = summary.lower()

    if "read_email" in s or "gmail" in s:
        if "unread" in s or "inbox" in s:
            return "scan_inbox", "Scanning inbox"
        if "from:" in s or "--full" in s:
            return "read_email", "Reading email thread"
        if "in:sent" in s:
            return "check_sent", "Checking sent history"
        if "subject:" in s:
            return "search_email", "Searching emails"
        return "read_email", "Reading emails"
    if "send_email" in s:
        return "send_email", "Sending email reply"
    if "search_memory" in s or "search -q" in s:
        return "recall", "Recalling memories"
    if "add_memory" in s:
        return "store", "Storing new memory"
    if "crm" in s or "leads.csv" in s or "activities.csv" in s:
        return "crm", "Updating CRM"
    if tool == "Edit":
        return "edit", "Editing file"
    if tool == "Write":
        return "write", "Writing file"
    if "grep" in s or "search" in s:
        return "search", "Searching context"
    if "git commit" in s or "git push" in s:
        return "commit", "Committing changes"
    return "action", "Working"


def _build_conversation(session_retrievals, steps, session_obs):
    """Build a conversation timeline from retrieval queries and observations.

    Retrieval queries contain user messages (the hook fires on each user prompt).
    Observations contain Claude's actions. We pair them into a chat timeline.

    All text is redacted: names replaced with fictional ones, paths removed,
    emails anonymized.
    """
    # Name replacement map — anonymize any real names in queries
    _name_map = {}
    _name_counter = [0]
    _fictional_names = ["Alex", "Sarah", "Marcus", "Elena", "James", "Nadia"]

    def _anonymize_name(match):
        name = match.group(0)
        if name.lower() not in _name_map:
            idx = _name_counter[0] % len(_fictional_names)
            _name_map[name.lower()] = _fictional_names[idx]
            _name_counter[0] += 1
        return _name_map[name.lower()]

    def _is_cyrillic(text):
        """Check if text is predominantly Cyrillic (non-English)."""
        cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
        return cyrillic > len(text) * 0.3

    def _translate_intent(text, next_obs=None):
        """Translate non-English user messages to English based on intent keywords.

        Uses keyword matching to produce a natural English equivalent.
        For a demo, this provides readable English without needing an LLM.
        """
        t = text.lower()

        # Common intent patterns (Ukrainian/Russian → English)
        if any(w in t for w in ["пошт", "email", "inbox", "mail", "лист"]):
            if any(w in t for w in ["відписал", "написал", "replied", "відповіл"]):
                return "Check the email? They replied. Write back and ask about the next steps."
            if any(w in t for w in ["перевір", "check", "подивись"]):
                return "Can you check the inbox for new messages?"
            return "Check the email and handle it."
        if any(w in t for w in ["давай", "go ahead", "ok", "ага", "так"]):
            return "OK, go ahead."
        if any(w in t for w in ["напиш", "write", "send", "відправ"]):
            return "Write and send the reply."
        if any(w in t for w in ["crm", "lead", "deal", "pipeline"]):
            return "Update the CRM with the latest info."
        if any(w in t for w in ["зроби", "do", "fix", "виправ"]):
            return "Make the changes we discussed."

        # Fallback: if still Cyrillic, summarize generically based on next action
        if _is_cyrillic(text):
            if next_obs:
                step_type, _ = _classify_step(next_obs)
                intent_map = {
                    "scan_inbox": "Check the inbox for new messages.",
                    "read_email": "Read that email thread.",
                    "search_email": "Search for the relevant emails.",
                    "send_email": "Send the reply.",
                    "recall": "Search our memory for context.",
                    "store": "Save this to memory.",
                    "crm": "Update the CRM.",
                    "edit": "Make the edits.",
                    "commit": "Commit the changes.",
                }
                return intent_map.get(step_type, "Handle this task.")
            return "Handle this task."

        return text

    def _clean_query(query):
        """Clean a retrieval query into a presentable user message."""
        if not query:
            return None
        # Retrieval queries often have system context prepended — extract user part
        # Look for natural language after system prefixes
        parts = query.split("\n")
        # Filter out lines that look like system context (paths, commands, etc.)
        user_lines = []
        for line in parts:
            line = line.strip()
            if not line:
                continue
            # Skip system-like lines
            if any(line.startswith(p) for p in ["/", "Ran:", "Edited ", "Wrote ", "- ", "**"]):
                continue
            if re.match(r"^[a-f0-9]{8,}", line):
                continue
            # Skip very short fragments
            if len(line) < 3:
                continue
            user_lines.append(line)

        text = " ".join(user_lines).strip()
        if not text or len(text) < 5:
            return None

        # Redact sensitive info
        text = _redact(text)
        return text

    def _describe_action(obs):
        """Generate a Claude response description from an observation."""
        summary = obs.get("summary", "")
        step_type, _ = _classify_step(obs)

        if step_type == "scan_inbox":
            return "Let me check the inbox for recent messages..."
        if step_type == "search_email":
            return "Searching for the relevant email thread..."
        if step_type == "read_email":
            return "Reading the full email conversation..."
        if step_type == "check_sent":
            return "Checking what was already sent to see the context..."
        if step_type == "send_email":
            return "Sending the reply now."
        if step_type == "recall":
            return "Searching memory for relevant context..."
        if step_type == "store":
            return "Saving this to memory for future reference."
        if step_type == "crm":
            return "Updating the CRM with the latest status..."
        if step_type == "edit":
            return "Making the requested changes..."
        if step_type == "write":
            return "Creating the file..."
        if step_type == "commit":
            return "Committing the changes..."
        return "Working on it..."

    conversation = []

    # Map retrieval timestamps to find which user messages correspond to which steps
    # Retrieval[0] = session start (auto, context from previous session)
    # Retrieval[1+] = user messages that triggered recall hooks

    used_retrievals = set()

    # Session start message
    conversation.append({
        "step_index": 0,
        "role": "system",
        "text": "Session started. Retrieving relevant memories from Q-weighted search...",
    })

    # Match user messages (from retrievals) to steps
    for r_idx, r in enumerate(session_retrievals):
        if r_idx == 0:
            continue  # skip session start auto-retrieval

        r_ts = r.get("timestamp", "")
        user_msg = _clean_query(r.get("query", ""))
        if not user_msg:
            continue

        # Find the step that this user message precedes
        matched_step = None
        matched_obs = None
        for step in steps:
            step_ts = step.get("timestamp", "")
            if step_ts and r_ts and step_ts >= r_ts and step.get("type") != "session_start":
                matched_step = step
                # Find the corresponding observation for context
                obs_idx = step["index"] - (1 if steps[0]["type"] == "session_start" else 0)
                if 0 <= obs_idx < len(session_obs):
                    matched_obs = session_obs[obs_idx]
                break

        step_idx = matched_step["index"] if matched_step else len(steps) - 1

        # Translate non-English messages to English for demo
        if _is_cyrillic(user_msg):
            user_msg = _translate_intent(user_msg, matched_obs)

        conversation.append({
            "step_index": step_idx,
            "role": "user",
            "text": user_msg,
        })
        used_retrievals.add(r_idx)

    # Add Claude action descriptions for each observation step
    for step in steps:
        if step["type"] in ("session_start", "session_end"):
            continue
        obs_idx = step["index"] - (1 if steps[0]["type"] == "session_start" else 0)
        if 0 <= obs_idx < len(session_obs):
            action_text = _describe_action(session_obs[obs_idx])
            conversation.append({
                "step_index": step["index"],
                "role": "assistant",
                "text": action_text,
            })

    # Session end message
    conversation.append({
        "step_index": len(steps) - 1,
        "role": "system",
        "text": "Session complete. Computing reward and updating Q-values for all retrieved memories.",
    })

    # Sort by step_index
    conversation.sort(key=lambda m: (m["step_index"], 0 if m["role"] == "user" else 1 if m["role"] == "assistant" else 2))

    return conversation


def _truncate(text, max_len=120):
    """Truncate text with ellipsis."""
    if not text or len(text) <= max_len:
        return text or ""
    return text[:max_len - 1] + "…"


def _summarize_actions(action_types):
    """Map action types to a readable English summary sentence.

    >>> _summarize_actions(["scan_inbox", "read_email", "check_sent"])
    "I'll handle this by checking the inbox, reading the email thread and checking sent history."
    """
    verb_map = {
        "scan_inbox": "checking the inbox",
        "read_email": "reading the email thread",
        "check_sent": "checking sent history",
        "search_email": "searching emails",
        "send_email": "sending the email reply",
        "recall": "recalling relevant memories",
        "store": "storing a new memory",
        "crm": "updating the CRM",
        "edit": "editing files",
        "write": "writing files",
        "search": "searching for context",
        "commit": "committing changes",
        "action": "working on it",
    }
    verbs = []
    seen = set()
    for t in action_types:
        verb = verb_map.get(t, "working on it")
        if verb not in seen:
            verbs.append(verb)
            seen.add(verb)
    if not verbs:
        return "Working on it."
    if len(verbs) == 1:
        return f"I'll handle this by {verbs[0]}."
    return "I'll handle this by " + ", ".join(verbs[:-1]) + " and " + verbs[-1] + "."


def _build_beats(steps, conversation, session_obs):
    """Group raw steps into narrative beats delimited by user messages.

    Returns a list of beat dicts with schema:
        id, type, title, subtitle, conversation, actions,
        memories_recalled, memories_count, step_indices,
        phase, reward_info, duration_hint
    """
    # Find user message step_indices from conversation
    user_msg_indices = []
    user_msgs = {}
    for msg in conversation:
        if msg["role"] == "user":
            user_msg_indices.append(msg["step_index"])
            user_msgs[msg["step_index"]] = msg["text"]
    user_msg_indices.sort()

    beats = []
    beat_id = 0

    # --- Beat 0: system_start ---
    start_steps = []
    start_conv = []
    for s in steps:
        if s["type"] == "session_start":
            start_steps.append(s)
    for msg in conversation:
        if msg["role"] == "system" and msg["step_index"] == 0:
            start_conv.append(msg)

    # Collect session-start memories — will be shown in first user_turn beat
    session_start_mems = []
    if start_steps:
        for s in start_steps:
            for m in s.get("memories_recalled", []):
                if m["id"] not in {x["id"] for x in session_start_mems}:
                    session_start_mems.append(m)

    beats.append({
        "id": beat_id,
        "type": "system_start",
        "title": "Session Start",
        "subtitle": "Waiting for user request...",
        "conversation": [{"role": m["role"], "text": m["text"]} for m in start_conv],
        "actions": [],
        "memories_recalled": [],
        "memories_count": 0,
        "step_indices": [s["index"] for s in start_steps],
        "phase": "start",
        "reward_info": None,
        "duration_hint": 2000,
    })
    beat_id += 1

    # --- Work steps (between start and end) ---
    work_steps = [s for s in steps if s["type"] not in ("session_start", "session_end")]

    if not user_msg_indices:
        # No user messages → single "auto" beat
        if work_steps:
            action_types = [s["type"] for s in work_steps]
            actions = []
            all_mems = list(session_start_mems)  # include session-start memories
            seen_mem_ids = {m["id"] for m in all_mems}
            for s in work_steps:
                _, label = _classify_step({"summary": s.get("description", ""), "tool": s.get("tool", "")})
                actions.append({"label": label, "type": s["type"], "step_index": s["index"]})
                for m in s.get("memories_recalled", []):
                    if m["id"] not in seen_mem_ids:
                        all_mems.append(m)
                        seen_mem_ids.add(m["id"])

            subtitle = _summarize_actions(action_types)
            beats.append({
                "id": beat_id,
                "type": "auto",
                "title": "Automated work",
                "subtitle": _truncate(subtitle, 150),
                "conversation": [{"role": "assistant", "text": subtitle, "summary": True}],
                "actions": actions,
                "memories_recalled": all_mems,
                "memories_count": len(all_mems),
                "step_indices": [s["index"] for s in work_steps],
                "phase": "work",
                "reward_info": None,
                "duration_hint": max(3500, len(actions) * 1200),
            })
            beat_id += 1
    else:
        # Group work steps by user messages
        # Each user message starts a new beat that includes all steps
        # until the next user message
        boundaries = user_msg_indices + [max(s["index"] for s in steps) + 1]

        for b_idx, boundary in enumerate(user_msg_indices):
            next_boundary = boundaries[b_idx + 1]
            user_text = user_msgs.get(boundary, "")

            # Steps in this beat: from this user message to next boundary
            beat_steps = [s for s in work_steps if boundary <= s["index"] < next_boundary]
            # Also include steps before first user message if this is the first user beat
            if b_idx == 0:
                pre_steps = [s for s in work_steps if s["index"] < boundary]
                beat_steps = pre_steps + beat_steps

            action_types = [s["type"] for s in beat_steps]
            actions = []
            # First user_turn gets session-start memories
            if b_idx == 0:
                all_mems = list(session_start_mems)
                seen_mem_ids = {m["id"] for m in all_mems}
            else:
                all_mems = []
                seen_mem_ids = set()
            for s in beat_steps:
                _, label = _classify_step({"summary": s.get("description", ""), "tool": s.get("tool", "")})
                actions.append({"label": label, "type": s["type"], "step_index": s["index"]})
                for m in s.get("memories_recalled", []):
                    if m["id"] not in seen_mem_ids:
                        all_mems.append(m)
                        seen_mem_ids.add(m["id"])

            subtitle = _summarize_actions(action_types) if action_types else ""

            beat_conv = [{"role": "user", "text": user_text}]
            if subtitle:
                beat_conv.append({"role": "assistant", "text": subtitle, "summary": True})

            # Generate a title from user text
            title = _truncate(user_text, 50) if user_text else "Continue work"

            beats.append({
                "id": beat_id,
                "type": "user_turn",
                "title": title,
                "subtitle": _truncate(subtitle, 150),
                "conversation": beat_conv,
                "actions": actions,
                "memories_recalled": all_mems,
                "memories_count": len(all_mems),
                "step_indices": [s["index"] for s in beat_steps],
                "phase": "work",
                "reward_info": None,
                "duration_hint": max(3500, len(actions) * 1200),
            })
            beat_id += 1

    # --- Final beat: system_end ---
    end_step = next((s for s in steps if s["type"] == "session_end"), None)
    end_conv = [msg for msg in conversation if msg["role"] == "system" and msg["step_index"] == len(steps) - 1]

    reward_info = end_step.get("reward_info") if end_step else None
    mem_updated = reward_info.get("memories_updated", 0) if reward_info else 0

    beats.append({
        "id": beat_id,
        "type": "system_end",
        "title": "Session Complete",
        "subtitle": f"{mem_updated} memories updated via Q-learning",
        "conversation": [{"role": m["role"], "text": m["text"]} for m in end_conv],
        "actions": [],
        "memories_recalled": [],
        "memories_count": 0,
        "step_indices": [end_step["index"]] if end_step else [],
        "phase": "reward",
        "reward_info": reward_info,
        "duration_hint": 5000,
    })

    return beats


def _clean_memory_preview(content, memory_type):
    """Clean and truncate memory content for display based on type.

    Session summaries contain raw logs — extract only the useful part.
    Other types get light cleanup with a generous length limit.
    """
    if not content:
        return ""

    # Session summaries: extract just the meaningful first line
    if memory_type in ("session_summary", "session"):
        # Try to find project/summary info
        lines = content.split("\n")
        for line in lines:
            line = line.strip().strip("#").strip("-").strip()
            if not line or len(line) < 10:
                continue
            # Skip raw code/JSON
            if any(c in line for c in ["{", "}", "json.load", "=", "(f)", "cache ="]):
                continue
            return _redact(_truncate(line, 150))
        return _redact(_truncate(content.split("\n")[0], 100))

    # Action observations: often start with "Ran: " — clean that
    if content.startswith("Ran: "):
        content = content[5:]

    return _redact(_truncate(content, 200))


def _build_scenario(session_obs):
    """Generate a narrative user story from session observations.

    Returns a dict with story paragraphs, success/failure criteria.
    The story is written for a general audience (HN/Reddit demo).
    """
    summaries = [o.get("summary", "").lower() for o in session_obs]

    has_email_read = any("email" in s or "gmail" in s or "inbox" in s for s in summaries)
    has_email_send = any("send_email" in s for s in summaries)
    has_crm = any("crm" in s or "leads" in s or "activities" in s for s in summaries)
    has_code = any(o.get("tool") in ("Edit", "Write") for o in session_obs)
    has_commit = any("git commit" in s or "git push" in s for s in summaries)
    n_actions = len(session_obs)

    # --- Build narrative story ---
    if has_email_read and has_email_send:
        title = "Can AI reply to email using past context?"
        story = (
            "A user asks their AI assistant to check the inbox and reply to an email thread. "
            "The catch: to write a good reply, the AI needs context from past conversations, "
            "deal history, and previous decisions — all stored as memories."
        )
        challenge = (
            "The system has hundreds of stored memories. It must find the RIGHT ones. "
            "This is where Q-learning kicks in: memories that helped in previous sessions "
            "have higher Q-values and rank first. Bad matches get penalized over time."
        )
    elif has_email_read:
        title = "Can AI process email with the right context?"
        story = (
            "A user asks their AI to check the inbox and handle incoming emails. "
            "To understand what matters, the AI needs context: who is this person? "
            "What's the history? What was discussed before?"
        )
        challenge = (
            "The system searches hundreds of stored memories to find relevant context. "
            "Memories ranked by Q-value — past usefulness determines what surfaces first."
        )
    elif has_code and has_commit:
        title = "Can AI write code using learned patterns?"
        story = (
            "A user asks their AI to make code changes and commit them. "
            "The AI needs to recall coding patterns, architecture decisions, "
            "and project conventions from past sessions."
        )
        challenge = (
            "The right context makes the difference between clean code and bugs. "
            "Q-learning ensures that helpful patterns rank higher over time."
        )
    elif has_crm:
        title = "Can AI manage CRM with full context?"
        story = (
            "A user asks their AI to update the CRM with latest deal status. "
            "The AI needs to recall deal history, contact details, and past interactions."
        )
        challenge = (
            "CRM updates require accurate context. Q-learning ensures the right "
            "deal context surfaces first, not outdated or irrelevant information."
        )
    else:
        title = "Can AI complete tasks using learned experience?"
        story = (
            f"A user gives their AI assistant a task requiring {n_actions} actions. "
            "The AI must recall relevant context from past sessions to do it well."
        )
        challenge = (
            "The system searches stored memories, ranked by Q-value. "
            "Each session, it learns which memories actually help — and which don't."
        )

    # Success / failure — concrete, short
    success = []
    failure = []
    if has_email_read:
        success.append("Finds relevant email context from memory")
    if has_email_send:
        success.append("Sends appropriate reply with full context")
    if has_crm:
        success.append("Updates CRM accurately")
    if has_code:
        success.append("Makes correct code changes")
    success.append("Q-values go UP for useful memories")

    if has_email_read:
        failure.append("Retrieves wrong context (wrong client, old deal)")
    if has_email_send:
        failure.append("Sends reply missing key details")
    failure.append("Q-values go DOWN for irrelevant memories")

    return {
        "title": title,
        "story": story,
        "challenge": challenge,
        "success_criteria": success,
        "failure_criteria": failure,
    }


def _build_outcome(session_obs, memory_q_values):
    """Generate session outcome verdict from observations and Q-value changes.

    Returns dict with verdict, achievements list, and key metrics.
    """
    summaries = [o.get("summary", "").lower() for o in session_obs]

    # Count concrete achievements
    achievements = []
    email_read = sum(1 for s in summaries if "email" in s and ("read" in s or "inbox" in s or "gmail" in s))
    email_sent = sum(1 for s in summaries if "send_email" in s)
    crm_ops = sum(1 for s in summaries if "crm" in s or "leads" in s or "activities" in s)
    files_mod = sum(1 for o in session_obs if o.get("tool") in ("Edit", "Write"))
    mem_stored = sum(1 for s in summaries if "add_memory" in s)
    commits = sum(1 for s in summaries if "git commit" in s)

    if email_read > 0:
        achievements.append(f"Email thread processed ({email_read} actions)")
    if email_sent > 0:
        achievements.append(f"Reply sent ({email_sent})")
    if crm_ops > 0:
        achievements.append(f"CRM updated ({crm_ops} ops)")
    if files_mod > 0:
        achievements.append(f"Files modified ({files_mod})")
    if commits > 0:
        achievements.append(f"Changes committed")
    if mem_stored > 0:
        achievements.append(f"New memories stored ({mem_stored})")

    if not achievements:
        achievements.append(f"{len(session_obs)} actions executed")

    # Verdict from reward direction
    positive = sum(1 for q in memory_q_values.values() if q.get("reward_direction") == "positive")
    negative = sum(1 for q in memory_q_values.values() if q.get("reward_direction") == "negative")
    total = len(memory_q_values)

    if positive > 0 and negative == 0:
        verdict = "productive"
        verdict_label = "Productive Session"
        verdict_emoji = "\u2705"
    elif positive > negative:
        verdict = "mostly_productive"
        verdict_label = "Mostly Productive"
        verdict_emoji = "\u2705"
    elif negative > positive * 2:
        verdict = "unproductive"
        verdict_label = "Needs Improvement"
        verdict_emoji = "\u26a0\ufe0f"
    else:
        verdict = "mixed"
        verdict_label = "Mixed Results"
        verdict_emoji = "\u2139\ufe0f"

    return {
        "verdict": verdict,
        "verdict_label": verdict_label,
        "verdict_emoji": verdict_emoji,
        "achievements": achievements,
        "metrics": {
            "actions_taken": len(session_obs),
            "memories_reinforced": positive,
            "memories_penalized": negative,
            "total_memories_updated": total,
        },
    }


def generate_demo_replay():
    """Generate a scripted demo replay with a realistic email-handling scenario.

    Returns the same structure as export_replay_data() but with handcrafted,
    anonymized content for a compelling HN/Reddit demo. Shows the full flow:
    email found → memory query → context loaded → reply drafted → user approves → sent.

    Rich conversation entries include content_type, flow states, and activity log.
    """
    from .core.q_value import DEFAULT_Q_CONFIG

    now = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Demo memories with realistic Q-values ---
    memory_q_values = {
        "a1b2c3d4": {
            "combined": 0.55, "combined_before": 0.42, "combined_delta": 0.13,
            "action": 0.58, "hypothesis": 0.50, "fit": 0.52,
            "visits": 7, "last_reward": 0.52,
            "reward_direction": "positive",
            "preview": "DataBridge Inc \u2014 $25K annual contract. Alex Chen is CTO. "
                       "Initial contact Jan 2026. They focus on computer vision pipelines.",
            "memory_type": "deal_context",
        },
        "b2c3d4e5": {
            "combined": 0.51, "combined_before": 0.38, "combined_delta": 0.13,
            "action": 0.54, "hypothesis": 0.45, "fit": 0.50,
            "visits": 4, "last_reward": 0.52,
            "reward_direction": "positive",
            "preview": "Alex Chen prefers quarterly billing. Budget approval needed "
                       "above $20K. Decision-maker is VP Engineering.",
            "memory_type": "client_preference",
        },
        "c3d4e5f6": {
            "combined": 0.72, "combined_before": 0.60, "combined_delta": 0.12,
            "action": 0.75, "hypothesis": 0.68, "fit": 0.70,
            "visits": 12, "last_reward": 0.52,
            "reward_direction": "positive",
            "preview": "Standard volume discount: 10% above 30K items/month, "
                       "15% above 50K items/month. Enterprise tier requires annual commitment.",
            "memory_type": "pricing_knowledge",
        },
        "d4e5f6a7": {
            "combined": 0.38, "combined_before": 0.25, "combined_delta": 0.13,
            "action": 0.40, "hypothesis": 0.35, "fit": 0.36,
            "visits": 3, "last_reward": 0.52,
            "reward_direction": "positive",
            "preview": "Previous email to DataBridge discussed their CV pipeline: "
                       "200K images/month, bounding box + classification. "
                       "Quality requirement: 98%+ accuracy.",
            "memory_type": "conversation_history",
        },
        "e5f6a7b8": {
            "combined": 0.46, "combined_before": 0.33, "combined_delta": 0.13,
            "action": 0.48, "hypothesis": 0.42, "fit": 0.44,
            "visits": 5, "last_reward": 0.52,
            "reward_direction": "positive",
            "preview": "DataBridge evaluated 3 vendors, chose us for labeling quality. "
                       "Contract renewal discussion planned for Q2 2026.",
            "memory_type": "deal_context",
        },
    }

    scenario = {
        "title": "Can AI reply to a client email using past deal context?",
        "story": (
            "A user asks their AI assistant to check the inbox. A client named Alex "
            "has replied about proposal pricing. To write a good reply, the AI needs "
            "to recall the deal history, pricing rules, and client preferences \u2014 "
            "all stored as Q-ranked memories from previous sessions."
        ),
        "challenge": (
            "The system has 847 stored memories. It must find the RIGHT 5 out of 847. "
            "This is where Q-learning kicks in: memories that helped in previous email "
            "sessions have higher Q-values and rank first. Irrelevant memories get "
            "penalized over time."
        ),
        "success_criteria": [
            "Finds the right client context from memory",
            "Applies correct pricing rules",
            "Sends a contextually accurate reply",
            "Q-values go UP for useful memories",
        ],
        "failure_criteria": [
            "Retrieves wrong client's deal history",
            "Misquotes pricing or terms",
            "Q-values go DOWN for irrelevant memories",
        ],
    }

    outcome = {
        "verdict": "productive",
        "verdict_label": "Productive Session",
        "verdict_emoji": "\u2705",
        "achievements": [
            "Email thread processed and replied",
            "5 relevant memories retrieved from 847 total",
            "Reply sent with correct pricing context",
            "All 5 memories reinforced (+Q)",
        ],
        "metrics": {
            "actions_taken": 6,
            "memories_reinforced": 5,
            "memories_penalized": 0,
            "total_memories_updated": 5,
        },
    }

    # --- Beats with rich conversation entries ---
    beats = [
        {
            "id": 0, "type": "system_start",
            "title": "Session Start",
            "subtitle": "Loading agent memory...",
            "conversation": [{
                "role": "system", "text": "Session started. Loading 847 memories "
                "from Q-weighted index...",
                "content_type": "text", "flow": ["claude_to_memory"],
                "activity": "\u2190 OpenExp: loaded 847 memories into search index",
            }],
            "actions": [], "memories_recalled": [], "memories_count": 0,
            "step_indices": [0], "phase": "start",
            "reward_info": None, "duration_hint": 2000,
        },
        {
            "id": 1, "type": "user_turn",
            "title": "Check inbox and handle email",
            "subtitle": "User asks to check inbox and handle reply",
            "conversation": [
                {
                    "role": "user",
                    "text": "Check the inbox \u2014 Alex from DataBridge should "
                            "have replied about the proposal pricing.",
                    "content_type": "text", "flow": ["user_to_claude"],
                    "activity": "\u2197 User request received",
                },
                {
                    "role": "assistant",
                    "text": "Checking inbox via Gmail API...",
                    "content_type": "text", "flow": ["claude_to_tools"],
                    "activity": "\u2192 Gmail API: querying inbox for recent messages",
                },
                {
                    "role": "assistant", "text": "",
                    "content_type": "email_card",
                    "email": {
                        "from": "Alex Chen (DataBridge Inc)",
                        "subject": "Re: Data Labeling Proposal \u2014 Pricing Question",
                        "date": "2 hours ago",
                        "snippet": (
                            "Hi, thanks for the detailed proposal. Before we sign, "
                            "can you clarify the volume discount structure? We're "
                            "looking at 50K items/month initially, with plans to "
                            "scale to 100K by Q3. Also, is quarterly billing an "
                            "option? Our finance team prefers that cycle."
                        ),
                    },
                    "flow": ["tools_to_claude"],
                    "activity": "\u2190 Gmail: found 1 new email from Alex Chen",
                },
                {
                    "role": "assistant",
                    "text": "Let me check our history with DataBridge...",
                    "content_type": "text", "flow": ["claude_to_memory"],
                    "activity": "\u2192 OpenExp: searching 'DataBridge deal history pricing'",
                },
                {
                    "role": "assistant", "text": "",
                    "content_type": "memory_results",
                    "query": "DataBridge deal history pricing",
                    "memories": [
                        {"id": "a1b2c3d4",
                         "preview": "DataBridge Inc \u2014 $25K annual contract. "
                                    "Alex Chen is CTO.",
                         "q_value": 0.42, "score": 0.89, "type": "deal_context"},
                        {"id": "c3d4e5f6",
                         "preview": "Volume discount: 10% above 30K, 15% above "
                                    "50K items/month.",
                         "q_value": 0.60, "score": 0.85, "type": "pricing_knowledge"},
                        {"id": "b2c3d4e5",
                         "preview": "Alex prefers quarterly billing. Budget "
                                    "approval needed above $20K.",
                         "q_value": 0.38, "score": 0.82, "type": "client_preference"},
                        {"id": "d4e5f6a7",
                         "preview": "Previous email: CV pipeline, 200K images/month.",
                         "q_value": 0.25, "score": 0.78,
                         "type": "conversation_history"},
                        {"id": "e5f6a7b8",
                         "preview": "Chose us over 2 vendors for quality. "
                                    "Renewal in Q2.",
                         "q_value": 0.33, "score": 0.75, "type": "deal_context"},
                    ],
                    "flow": ["memory_to_claude"],
                    "activity": "\u2190 OpenExp: 5 memories found (best Q: 0.60)",
                },
                {
                    "role": "assistant",
                    "text": (
                        "Based on our deal context with DataBridge:\n\n"
                        "\u2022 Alex Chen is CTO, $25K annual contract\n"
                        "\u2022 They prefer quarterly billing "
                        "(his finance team's preference)\n"
                        "\u2022 Standard discount: 15% for 50K items/month volume\n"
                        "\u2022 Their CV pipeline processes 200K images/month\n"
                        "\u2022 They chose us over 2 other vendors for quality\n\n"
                        "I recommend: confirm the 15% volume discount for 50K+ items, "
                        "offer quarterly billing as he requested, and mention the Q2 "
                        "renewal timeline."
                    ),
                    "content_type": "text", "flow": ["claude_to_user"],
                    "activity": "\u2199 Claude: analysis complete, recommendation ready",
                },
            ],
            "actions": [
                {"label": "Querying Gmail API for recent messages",
                 "type": "scan_inbox", "step_index": 1},
                {"label": "Found: 'Re: Data Labeling Proposal \u2014 Pricing'",
                 "type": "read_email", "step_index": 2},
                {"label": "Searching OpenExp: 'DataBridge deal history pricing'",
                 "type": "recall", "step_index": 3},
                {"label": "Retrieved 5 memories (best Q: 0.60)",
                 "type": "recall", "step_index": 4},
                {"label": "Analyzing deal context and pricing rules",
                 "type": "action", "step_index": 5},
            ],
            "memories_recalled": [
                {"id": "a1b2c3d4", "score": 0.89, "q_combined": 0.42},
                {"id": "c3d4e5f6", "score": 0.85, "q_combined": 0.60},
                {"id": "b2c3d4e5", "score": 0.82, "q_combined": 0.38},
                {"id": "d4e5f6a7", "score": 0.78, "q_combined": 0.25},
                {"id": "e5f6a7b8", "score": 0.75, "q_combined": 0.33},
            ],
            "memories_count": 5,
            "step_indices": [1, 2, 3, 4, 5],
            "phase": "work", "reward_info": None, "duration_hint": 8000,
        },
        {
            "id": 2, "type": "user_turn",
            "title": "Send the reply",
            "subtitle": "User approves the draft",
            "conversation": [
                {
                    "role": "user",
                    "text": "Sounds good, send it.",
                    "content_type": "text", "flow": ["user_to_claude"],
                    "activity": "\u2197 User: approved",
                },
                {
                    "role": "assistant",
                    "text": "Drafting and sending the reply...",
                    "content_type": "text", "flow": ["claude_to_tools"],
                    "activity": "\u2192 Gmail API: composing and sending reply",
                },
                {
                    "role": "assistant", "text": "",
                    "content_type": "email_sent",
                    "email": {
                        "to": "Alex Chen (DataBridge Inc)",
                        "subject": "Re: Data Labeling Proposal \u2014 "
                                   "Pricing Question",
                        "snippet": (
                            "Hi Alex,\n\n"
                            "Great questions! For your 50K items/month volume, "
                            "I'm happy to confirm our 15% volume discount \u2014 "
                            "that brings your per-item rate to $0.034.\n\n"
                            "Quarterly billing is absolutely available. I'll update "
                            "the proposal with Q1 billing terms.\n\n"
                            "Looking forward to finalizing before the Q2 renewal "
                            "cycle.\n\nBest regards"
                        ),
                        "status": "sent",
                    },
                    "flow": ["tools_to_claude"],
                    "activity": "\u2190 Gmail: reply sent successfully",
                },
                {
                    "role": "assistant",
                    "text": "Reply sent successfully. Saving this interaction "
                            "to memory for future reference.",
                    "content_type": "text", "flow": ["claude_to_memory"],
                    "activity": "\u2192 OpenExp: storing interaction as new memory",
                },
            ],
            "actions": [
                {"label": "Composing reply with pricing context",
                 "type": "action", "step_index": 6},
                {"label": "Sending via Gmail API",
                 "type": "send_email", "step_index": 7},
                {"label": "Saving interaction to OpenExp memory",
                 "type": "store", "step_index": 8},
            ],
            "memories_recalled": [], "memories_count": 0,
            "step_indices": [6, 7, 8],
            "phase": "work", "reward_info": None, "duration_hint": 5000,
        },
        {
            "id": 3, "type": "system_end",
            "title": "Session Complete",
            "subtitle": "5 memories reinforced via Q-learning",
            "conversation": [{
                "role": "system",
                "text": "Session complete. Computing reward and updating "
                        "Q-values for all 5 retrieved memories.",
                "content_type": "text", "flow": ["claude_to_memory"],
                "activity": "\u2190 Q-learning: reward applied to 5 memories",
            }],
            "actions": [], "memories_recalled": [], "memories_count": 0,
            "step_indices": [9], "phase": "reward",
            "reward_info": {"memories_updated": 5, "alpha": 0.25},
            "duration_hint": 5000,
        },
    ]

    # Steps (backward compat)
    steps = [
        {"index": i, "timestamp": now, "type": t, "label": l,
         "description": d, "phase": p}
        for i, (t, l, d, p) in enumerate([
            ("session_start", "Session Start",
             "Retrieved 5 memories from Q-weighted search", "recall"),
            ("scan_inbox", "Scanning inbox",
             "Querying Gmail API for recent messages", "work"),
            ("read_email", "Reading email",
             "Found email from Alex Chen about pricing", "work"),
            ("recall", "Memory search",
             "Searching OpenExp for DataBridge deal history", "recall"),
            ("recall", "Memory results",
             "Retrieved 5 memories (best Q: 0.60)", "recall"),
            ("action", "Analysis",
             "Analyzing deal context and drafting response", "work"),
            ("action", "Composing",
             "Composing reply with pricing context", "work"),
            ("send_email", "Sending email",
             "Sending reply via Gmail API", "work"),
            ("store", "Saving memory",
             "Saving interaction to OpenExp memory", "work"),
            ("session_end", "Session End",
             "Observations ingested, Q-values updated", "reward"),
        ])
    ]
    steps[-1]["reward_info"] = {"memories_updated": 5, "alpha": 0.25}

    conversation = [
        {"step_index": 0, "role": "system",
         "text": "Session started. Loading 847 memories..."},
        {"step_index": 1, "role": "user",
         "text": "Check the inbox \u2014 Alex from DataBridge should have "
                 "replied about the proposal pricing."},
        {"step_index": 5, "role": "assistant",
         "text": "I'll handle this by checking the inbox, reading the email "
                 "thread and recalling relevant memories."},
        {"step_index": 6, "role": "user", "text": "Sounds good, send it."},
        {"step_index": 7, "role": "assistant",
         "text": "Sending the reply now."},
        {"step_index": 9, "role": "system",
         "text": "Session complete. 5 memories updated via Q-learning."},
    ]

    return {
        "meta": {
            "session_id": "demo0001",
            "generated_at": now,
            "date": today,
            "total_steps": len(steps),
            "total_observations": 8,
            "memories_retrieved": 5,
            "total_beats": len(beats),
            "project": "demo",
            "demo": True,
        },
        "scenario": scenario,
        "outcome": outcome,
        "steps": steps,
        "conversation": conversation,
        "beats": beats,
        "memory_q_values": memory_q_values,
        "q_config": {
            "alpha": DEFAULT_Q_CONFIG["alpha"],
            "q_floor": DEFAULT_Q_CONFIG["q_floor"],
            "q_ceiling": DEFAULT_Q_CONFIG["q_ceiling"],
            "layer_weights": {
                "action": DEFAULT_Q_CONFIG["q_action_weight"],
                "hypothesis": DEFAULT_Q_CONFIG["q_hypothesis_weight"],
                "fit": DEFAULT_Q_CONFIG["q_fit_weight"],
            },
        },
    }


def export_replay_data(session_id):
    """Export a single session as a step-by-step replay timeline.

    Args:
        session_id: Full or prefix of session UUID.

    Returns:
        dict with replay timeline, retrieval snapshots, and Q-value changes.
    """
    from .core.config import DATA_DIR, Q_CACHE_PATH, OBSERVATIONS_DIR, SESSIONS_DIR
    from .core.q_value import QCache, DEFAULT_Q_CONFIG

    # --- Load Q-cache ---
    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)
    cache = q_cache._cache

    # --- Find observations for this session ---
    obs_dir = Path(OBSERVATIONS_DIR)
    session_obs = []
    full_session_id = None

    if obs_dir.exists():
        for f in sorted(obs_dir.glob("observations-*.jsonl")):
            for entry in _load_jsonl(f):
                sid = entry.get("session_id", "")
                if sid.startswith(session_id):
                    full_session_id = sid
                    session_obs.append(entry)

    if not session_obs:
        return {"error": f"No observations found for session {session_id}"}

    session_obs.sort(key=lambda x: x.get("timestamp", ""))

    # --- Load retrievals for this session ---
    retrievals_path = DATA_DIR / "session_retrievals.jsonl"
    session_retrievals = []
    for r in _load_jsonl(retrievals_path):
        if r.get("session_id", "").startswith(session_id):
            session_retrievals.append(r)
    session_retrievals.sort(key=lambda x: x.get("timestamp", ""))

    # Collect all retrieved memory IDs and their Q-values
    all_memory_ids = set()
    for r in session_retrievals:
        all_memory_ids.update(r.get("memory_ids", []))

    # --- Fetch memory content previews from Qdrant ---
    memory_previews = {}
    try:
        from .core.config import COLLECTION_NAME, QDRANT_HOST, QDRANT_PORT
        from qdrant_client import QdrantClient
        qc = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=5)
        for mid in all_memory_ids:
            try:
                pts = qc.retrieve(
                    collection_name=COLLECTION_NAME,
                    ids=[mid],
                    with_payload=["memory", "memory_type"],
                )
                if pts:
                    content = pts[0].payload.get("memory", "")
                    mtype = pts[0].payload.get("memory_type", "fact")
                    preview = _clean_memory_preview(content, mtype)
                    memory_previews[mid[:8]] = {"preview": preview, "type": mtype}
            except Exception:
                continue
    except Exception:
        pass  # Qdrant not available — no previews, degrade gracefully

    memory_q_values = {}
    alpha = DEFAULT_Q_CONFIG["alpha"]
    for mid in all_memory_ids:
        q_nested = cache.get(mid)
        q = q_nested.get("default") if isinstance(q_nested, dict) and "default" in q_nested else q_nested
        if q:
            combined = q.get("q_value", 0)
            last_reward = q.get("last_reward", 0) or 0
            action_val = q.get("q_action", 0)
            hyp_val = q.get("q_hypothesis", 0.5)
            fit_val = q.get("q_fit", 0.5)

            # Estimate before-session values by reversing the last reward
            action_w = DEFAULT_Q_CONFIG["q_action_weight"]
            combined_delta = round(action_w * alpha * last_reward, 4)
            combined_before = round(combined - combined_delta, 3)

            preview_info = memory_previews.get(mid[:8], {})

            memory_q_values[mid[:8]] = {
                "combined": round(combined, 3),
                "combined_before": combined_before,
                "combined_delta": combined_delta,
                "action": round(action_val, 3),
                "hypothesis": round(hyp_val, 3),
                "fit": round(fit_val, 3),
                "visits": q.get("q_visits", 0),
                "last_reward": round(last_reward, 3),
                "reward_direction": "positive" if last_reward > 0 else "negative" if last_reward < 0 else "neutral",
                "preview": preview_info.get("preview", ""),
                "memory_type": preview_info.get("type", ""),
            }

    # --- Build timeline steps ---
    steps = []

    # Step 0: Session Start + initial retrieval
    if session_retrievals:
        r = session_retrievals[0]
        mem_ids = r.get("memory_ids", [])
        scores = r.get("scores", [])
        recalled = []
        for i, mid in enumerate(mem_ids):
            score = scores[i] if i < len(scores) else 0
            q = memory_q_values.get(mid[:8], {})
            recalled.append({
                "id": mid[:8],
                "score": round(score, 3),
                "q_combined": q.get("combined", 0),
            })

        steps.append({
            "index": 0,
            "timestamp": r.get("timestamp", session_obs[0]["timestamp"]),
            "type": "session_start",
            "label": "Session Start",
            "description": f"Retrieved {len(mem_ids)} memories from Q-weighted search",
            "memories_recalled": recalled[:6],
            "phase": "recall",
        })

    # Steps for each observation
    for i, obs in enumerate(session_obs):
        step_type, label = _classify_step(obs)
        summary = _redact(obs.get("summary", ""))

        # Check if there's a retrieval around this time (user message recall)
        mid_retrievals = []
        for r in session_retrievals[1:]:
            r_ts = r.get("timestamp", "")
            o_ts = obs.get("timestamp", "")
            if r_ts and o_ts and r_ts <= o_ts:
                mids = r.get("memory_ids", [])
                scores = r.get("scores", [])
                for j, mid in enumerate(mids[:4]):
                    sc = scores[j] if j < len(scores) else 0
                    q = memory_q_values.get(mid[:8], {})
                    mid_retrievals.append({
                        "id": mid[:8],
                        "score": round(sc, 3),
                        "q_combined": q.get("combined", 0),
                    })
                break

        step = {
            "index": len(steps),
            "timestamp": obs.get("timestamp", ""),
            "type": step_type,
            "label": label,
            "description": summary[:200],
            "tool": obs.get("tool", ""),
            "obs_type": obs.get("type", ""),
            "phase": "work",
        }
        if mid_retrievals:
            step["memories_recalled"] = mid_retrievals
            step["phase"] = "recall"

        steps.append(step)

    # Final step: Session End + reward
    steps.append({
        "index": len(steps),
        "timestamp": session_obs[-1]["timestamp"] if session_obs else "",
        "type": "session_end",
        "label": "Session End",
        "description": "Observations ingested, session reward computed, Q-values updated",
        "phase": "reward",
        "reward_info": {
            "memories_updated": len(all_memory_ids),
            "alpha": DEFAULT_Q_CONFIG["alpha"],
        },
    })

    # --- Session summary ---
    sess_dir = Path(SESSIONS_DIR)
    session_summary = None
    if sess_dir.exists():
        for f in sess_dir.glob("*.md"):
            if session_id in f.name:
                session_summary = f.read_text()[:500]
                # Redact paths in summary
                session_summary = _redact(session_summary)
                break

    # --- Build conversation from retrieval queries ---
    conversation = _build_conversation(session_retrievals, steps, session_obs)

    # --- Build narrative beats ---
    beats = _build_beats(steps, conversation, session_obs)

    # --- Build scenario and outcome ---
    scenario = _build_scenario(session_obs)
    outcome = _build_outcome(session_obs, memory_q_values)

    data = {
        "meta": {
            "session_id": full_session_id[:8] if full_session_id else session_id[:8],
            "generated_at": datetime.now().isoformat(),
            "date": _parse_date(session_obs[0]["timestamp"]) if session_obs else None,
            "total_steps": len(steps),
            "total_observations": len(session_obs),
            "memories_retrieved": len(all_memory_ids),
            "total_beats": len(beats),
            "project": session_obs[0].get("project", "") if session_obs else "",
        },
        "scenario": scenario,
        "outcome": outcome,
        "steps": steps,
        "conversation": conversation,
        "beats": beats,
        "memory_q_values": memory_q_values,
        "q_config": {
            "alpha": DEFAULT_Q_CONFIG["alpha"],
            "q_floor": DEFAULT_Q_CONFIG["q_floor"],
            "q_ceiling": DEFAULT_Q_CONFIG["q_ceiling"],
            "layer_weights": {
                "action": DEFAULT_Q_CONFIG["q_action_weight"],
                "hypothesis": DEFAULT_Q_CONFIG["q_hypothesis_weight"],
                "fit": DEFAULT_Q_CONFIG["q_fit_weight"],
            },
        },
    }

    _sanitize(data)
    return data


def find_best_replay_session():
    """Find the most interesting session for replay demo.

    Prefers sessions with email + memory recall + CRM activity.
    Returns session_id prefix or None.
    """
    from .core.config import OBSERVATIONS_DIR

    obs_dir = Path(OBSERVATIONS_DIR)
    if not obs_dir.exists():
        return None

    # Score each session by "interestingness"
    session_scores = defaultdict(lambda: {"count": 0, "email": 0, "memory": 0, "crm": 0, "date": ""})

    for f in sorted(obs_dir.glob("observations-*.jsonl")):
        for entry in _load_jsonl(f):
            sid = entry.get("session_id", "")
            if not sid:
                continue
            s = session_scores[sid]
            s["count"] += 1
            summary = entry.get("summary", "").lower()
            if "email" in summary or "gmail" in summary or "send_email" in summary:
                s["email"] += 1
            if "search_memory" in summary or "add_memory" in summary:
                s["memory"] += 1
            if "crm" in summary or "leads" in summary or "activities" in summary:
                s["crm"] += 1
            ts = entry.get("timestamp", "")
            if ts > s["date"]:
                s["date"] = ts

    # Rank: prefer diverse sessions (email + memory + crm) with recent dates
    ranked = sorted(
        session_scores.items(),
        key=lambda x: (
            min(x[1]["email"], 1) + min(x[1]["memory"], 1) + min(x[1]["crm"], 1),
            x[1]["count"],
            x[1]["date"],
        ),
        reverse=True,
    )

    if ranked:
        return ranked[0][0]
    return None


def _sanitize(data):
    """Assert no string values contain file paths or sensitive patterns."""
    sensitive_patterns = [
        r"/Users/\w+",
        r"/home/\w+",
        r"sk-ant-",
        r"sk-[a-zA-Z0-9]{20,}",
    ]

    def _check(obj, path=""):
        if isinstance(obj, str):
            for pat in sensitive_patterns:
                if re.search(pat, obj, re.IGNORECASE):
                    raise ValueError(
                        f"Sensitive data found at {path}: matches pattern '{pat}'"
                    )
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _check(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _check(v, f"{path}[{i}]")

    _check(data)

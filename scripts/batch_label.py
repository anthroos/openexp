"""Batch label all threads — extract experience labels via Opus and store in Qdrant.

Usage:
    cd ~/openexp
    .venv/bin/python3 scripts/batch_label.py [--force] [--thread-ids 1 2 3]
"""
import json
import glob
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openexp.core.direct_search import add_experience
from openexp.core.q_value import QCache
from openexp.core.config import Q_CACHE_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

CHUNKS_DIR = Path(os.path.expanduser("~/.openexp/data/chunks"))
THREADS_DIR = CHUNKS_DIR / "threads"

EXPERIENCE_PROMPT = """\
You are a DATA LABELER for an experience learning system.

You are analyzing a WORK THREAD — a continuous stream of work on one project/deal/initiative.
Your job: extract STRUCTURED EXPERIENCE from the raw conversation data.

## Thread metadata
{thread_json}

## What you must produce

### 1. TIMELINE
Chronological sequence of events. Each event:
- date: YYYY-MM-DD
- event_type: task_started | decision | milestone | problem | client_interaction | delivery | pivot | context
- title: short title
- description: what happened (specific — names, numbers, technical details)
- decisions_made: [list of decisions, if any]
- context: what was happening around this time
- outcome: what resulted

### 2. EXPERIENCE LABELS
For each meaningful segment of work, extract:
{{
  "experience_id": "exp_XXX",
  "context": {{
    "situation": "What was the situation when this started",
    "constraints": ["Time pressure", "Budget limit", etc],
    "stakeholders": ["Who was involved and their role"],
    "prior_knowledge": "What we knew going in"
  }},
  "actions": [
    {{"what": "Specific action taken", "why": "Reasoning", "when": "YYYY-MM-DD"}}
  ],
  "outcome": {{
    "result": "What happened",
    "success": true/false/null,
    "metrics": "Numbers if available",
    "surprise": "What was unexpected"
  }},
  "lesson": {{
    "insight": "One-sentence transferable insight",
    "applies_when": "When to use this lesson",
    "anti_pattern": "What NOT to do"
  }}
}}

### 3. THREAD SUMMARY
- status: completed | ongoing | success | failure | abandoned
- outcome_summary: overall result
- total_duration_days: number
- key_decisions: most important decisions
- financial: revenue/cost if mentioned
- people: who was involved

## Rules
- Be SPECIFIC. "Sent proposal within 24h" not "responded quickly"
- 3-15 experience labels per thread is normal
- "applies_when" is critical — tells WHEN this experience is relevant
- Include ALL context — don't lose information

Return ONLY valid JSON: {{"timeline": [...], "experiences": [...], "summary": {{...}}}}
"""


def _build_keywords(thread: dict) -> set:
    """Build keyword set from topic names (>2 chars to catch CRM, bot, MCP)."""
    keywords = set()
    for name in thread.get("topic_names", []):
        for word in name.lower().replace("-", " ").replace("_", " ").split():
            if len(word) > 2:
                keywords.add(word)
    return keywords


def _extract_thread_text(thread: dict, max_chars: int = 80_000) -> str:
    """Gather relevant messages for a thread from chunks."""
    keywords = _build_keywords(thread)
    if not keywords:
        return ""

    # Require fewer matches for threads with few keywords
    min_matches = 1 if len(keywords) <= 2 else 2

    def is_relevant(text: str) -> bool:
        t_lower = text.lower()
        return sum(1 for kw in keywords if kw in t_lower) >= min_matches

    lines = []
    total = 0

    for cid in sorted(thread.get("chunks", [])):
        chunk_file = CHUNKS_DIR / f"chunk_{cid:03d}.json"
        if not chunk_file.exists():
            continue
        chunk = json.loads(chunk_file.read_text())
        for session in chunk.get("sessions", []):
            msgs = session.get("messages", [])
            session_text = " ".join(m.get("memory", "") for m in msgs)
            if not is_relevant(session_text):
                continue

            relevant_indices = {i for i, m in enumerate(msgs)
                                if m.get("memory") and is_relevant(m["memory"])}
            # Include assistant responses after relevant user messages
            for i, m in enumerate(msgs):
                if (m.get("memory") and i not in relevant_indices
                        and m.get("role") == "assistant"
                        and (i - 1) in relevant_indices):
                    relevant_indices.add(i)
            relevant = [msgs[i] for i in sorted(relevant_indices)]

            if not relevant:
                continue

            date = relevant[0].get("created_at", "")[:10]
            header = f"\n=== {date} | chunk {cid} | {len(relevant)} messages ==="
            lines.append(header)
            total += len(header)

            # Sample: first 5 + last 3 if > 10
            if len(relevant) > 10:
                sample = relevant[:5] + [{"role": "system", "memory": f"... [{len(relevant) - 8} messages omitted] ..."}] + relevant[-3:]
            else:
                sample = relevant

            for m in sample:
                mem = m.get("memory", "")[:500]
                role = m.get("role", "?")
                label = "IVAN" if role == "user" else ("ASSISTANT" if role == "assistant" else "")
                entry = f"{label}: {mem}\n" if label else f"{mem}\n"
                if total + len(entry) > max_chars:
                    lines.append("... [truncated] ...")
                    return "\n".join(lines)
                lines.append(entry)
                total += len(entry)

    return "\n".join(lines)


def _call_opus(prompt: str, timeout: int = 300) -> str:
    """Call Opus via claude -p."""
    env = {**os.environ, "OPENEXP_EXTRACT_RUNNING": "1"}
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "opus"],
            input=prompt, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        log.error("claude -p timed out after %ds (%d chars prompt)", timeout, len(prompt))
        return ""
    if result.returncode != 0:
        log.error("claude -p failed (exit=%d): %s", result.returncode, result.stderr[:300])
        return ""
    return result.stdout.strip()


def _parse_json(text: str):
    """Parse JSON from LLM response."""
    if not text:
        return None
    t = text
    if "```json" in t:
        t = t.split("```json")[1].split("```")[0]
    elif "```" in t:
        t = t.split("```")[1].split("```")[0]
    return json.loads(t.strip())


def label_thread(thread: dict, q_cache: QCache, force: bool = False) -> dict:
    """Label one thread: extract → Opus → save → Qdrant. Returns stats."""
    tid = thread["thread_id"]
    name = thread["name"]
    safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in name)[:50].strip().replace(" ", "_")
    out_file = THREADS_DIR / f"thread_{tid:03d}_{safe}.json"

    # Skip if already done
    if out_file.exists() and not force:
        data = json.loads(out_file.read_text())
        n_exp = len(data.get("experiences", []))
        log.info("Thread %d: already labeled (%d labels), skip", tid, n_exp)
        return {"thread_id": tid, "name": name, "status": "skipped", "labels": n_exp}

    # Extract text
    thread_text = _extract_thread_text(thread)
    if len(thread_text) < 200:
        log.warning("Thread %d: too little data (%d chars), skip", tid, len(thread_text))
        return {"thread_id": tid, "name": name, "status": "low_data", "labels": 0}

    # Call Opus
    prompt = EXPERIENCE_PROMPT.format(thread_json=json.dumps(thread, ensure_ascii=False, indent=2))
    full_prompt = f"{prompt}\n\n---\n\nRAW CONVERSATION DATA:\n\n{thread_text}"
    log.info("Thread %d (%s): %d chars → Opus...", tid, name[:40], len(thread_text))

    t0 = time.time()
    response = _call_opus(full_prompt, timeout=360)
    elapsed = time.time() - t0

    if not response:
        log.error("Thread %d: Opus returned empty", tid)
        return {"thread_id": tid, "name": name, "status": "opus_failed", "labels": 0}

    # Parse
    try:
        data = _parse_json(response)
    except (json.JSONDecodeError, TypeError) as e:
        log.error("Thread %d: JSON parse failed: %s", tid, e)
        # Save raw for debugging
        (THREADS_DIR / f"thread_{tid:03d}_RAW.txt").write_text(response)
        return {"thread_id": tid, "name": name, "status": "parse_failed", "labels": 0}

    data["thread_id"] = tid
    data["thread_name"] = name

    # Save JSON
    with open(out_file, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Store in Qdrant
    experiences = data.get("experiences", [])
    stored = 0
    for exp in experiences:
        try:
            add_experience(exp, thread_id=tid, thread_name=name, q_cache=q_cache)
            stored += 1
        except Exception as e:
            log.error("Thread %d exp %s: Qdrant failed: %s", tid, exp.get("experience_id"), e)

    log.info("Thread %d: %d timeline events, %d labels stored (%.0fs)",
             tid, len(data.get("timeline", [])), stored, elapsed)

    return {
        "thread_id": tid,
        "name": name,
        "status": "labeled",
        "labels": stored,
        "timeline_events": len(data.get("timeline", [])),
        "elapsed_s": round(elapsed),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--thread-ids", type=int, nargs="*")
    args = parser.parse_args()

    threads_file = CHUNKS_DIR / "threads.json"
    if not threads_file.exists():
        print(f"Error: {threads_file} not found. Run thread grouping first.", file=sys.stderr)
        sys.exit(1)
    threads = json.loads(threads_file.read_text())
    # Sort by total_messages desc
    threads.sort(key=lambda t: t.get("total_messages", 0), reverse=True)

    THREADS_DIR.mkdir(exist_ok=True)
    q_cache = QCache(Q_CACHE_PATH)

    results = []
    total_labels = 0

    for i, thread in enumerate(threads):
        tid = thread["thread_id"]
        if args.thread_ids and tid not in args.thread_ids:
            continue

        result = label_thread(thread, q_cache, force=args.force)
        results.append(result)
        total_labels += result.get("labels", 0)

        # Save Q-cache every 5 threads
        if (i + 1) % 5 == 0:
            q_cache.save(Q_CACHE_PATH)
            log.info("--- Checkpoint: %d/%d threads, %d labels total ---",
                     i + 1, len(threads), total_labels)

    # Final save
    q_cache.save(Q_CACHE_PATH)

    # Summary
    summary = {
        "total_threads": len(threads),
        "labeled": len([r for r in results if r["status"] == "labeled"]),
        "skipped": len([r for r in results if r["status"] == "skipped"]),
        "low_data": len([r for r in results if r["status"] == "low_data"]),
        "failed": len([r for r in results if r["status"] in ("opus_failed", "parse_failed")]),
        "total_labels": total_labels,
        "results": results,
    }
    summary_file = THREADS_DIR / "batch_summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE")
    print(f"  Labeled: {summary['labeled']}")
    print(f"  Skipped (already done): {summary['skipped']}")
    print(f"  Low data: {summary['low_data']}")
    print(f"  Failed: {summary['failed']}")
    print(f"  Total experience labels: {total_labels}")
    print(f"  Summary: {summary_file}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

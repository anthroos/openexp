"""Experience Extraction — outcome-driven labeling of conversation data.

NOT topic grouping. Everyone does topics. We label data relative to
SUCCESS and FAILURE outcomes, then trace the full journey for each.

Pipeline:
  1. threads.json already exists (56 threads from topic grouping)
  2. For each thread → gather ALL raw messages chronologically
  3. Opus builds structured timeline + extracts experience labels
  4. Experience = {context, actions, outcome} — training data format

Output format is designed for:
  - NOW: experience layer as system prompt (skill queries OpenExp → gets relevant experience)
  - LATER: LoRA fine-tuning data (context→actions→outcome triplets)

Uses claude -p (Max subscription, Opus) — quality IS the product.
"""
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CHUNKS_DIR_NAME = "chunks"
THREADS_DIR_NAME = "threads"

# System prompt for experience extraction — the core labeling engine.
# This prompt turns raw conversation data into structured experience.
EXPERIENCE_EXTRACTION_PROMPT = """\
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
This is the KEY output. For each meaningful segment of work, extract:
```
{{
  "experience_id": "exp_XXX",
  "context": {{
    "situation": "What was the situation when this started",
    "constraints": ["Time pressure", "Budget limit", etc],
    "stakeholders": ["Who was involved and their role"],
    "prior_knowledge": "What we knew going in"
  }},
  "actions": [
    {{
      "what": "Specific action taken",
      "why": "Reasoning behind it",
      "when": "YYYY-MM-DD"
    }}
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
    "anti_pattern": "What NOT to do (if learned from failure)"
  }}
}}
```

### 3. THREAD SUMMARY
- status: completed | ongoing | success | failure | abandoned
- outcome_summary: what was the overall result
- total_duration_days: number
- key_decisions: most important decisions
- financial: revenue/cost if mentioned
- people: who was involved

## Rules
- Be SPECIFIC, not generic. "Sent proposal within 24h" not "responded quickly"
- Extract EVERY experience label you can find — 3 to 15 per thread is normal
- Experience labels are TRAINING DATA — they need to be precise enough that an LLM could learn the pattern
- The "applies_when" field is critical — it tells the model WHEN this experience is relevant
- Include ALL raw data context — don't lose information
- If financial data exists, always include it

Return JSON: {{"timeline": [...], "experiences": [...], "summary": {{...}}}}
"""


def _call_opus(prompt: str, timeout: int = 300) -> str:
    """Call Opus via claude -p (Max subscription). Returns response text."""
    env = {**os.environ, "OPENEXP_EXTRACT_RUNNING": "1"}
    env.pop("ANTHROPIC_API_KEY", None)

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "opus"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.error("claude -p timed out after %ds (%d chars prompt)", timeout, len(prompt))
        return ""

    if result.returncode != 0:
        logger.error("claude -p failed (exit=%d): %s", result.returncode, result.stderr[:500])
        return ""

    return result.stdout.strip()


def _parse_json(text: str) -> Optional[list | dict]:
    """Parse JSON from LLM response, handling markdown wrapping."""
    if not text:
        return None
    json_text = text
    if "```json" in json_text:
        json_text = json_text.split("```json")[1].split("```")[0]
    elif "```" in json_text:
        json_text = json_text.split("```")[1].split("```")[0]
    return json.loads(json_text.strip())


def _gather_thread_messages(
    thread: dict, chunks_dir: Path, max_chars: int = 100_000
) -> str:
    """Gather ALL messages for a thread from its chunks, chronologically.

    Uses keyword matching on topic names to find relevant sessions,
    then extracts messages with smart sampling to stay within budget.
    """
    chunk_ids = thread.get("chunks", [])
    topic_names = [n.lower() for n in thread.get("topic_names", [])]

    # Build keyword set from topic names (keep words >2 chars to catch CRM, bot, MCP)
    keywords = set()
    for name in topic_names:
        for word in name.replace("-", " ").replace("_", " ").split():
            if len(word) > 2:
                keywords.add(word.lower())

    # Require fewer matches for threads with few keywords
    min_matches = 1 if len(keywords) <= 2 else 2

    def is_relevant(text: str) -> bool:
        t_lower = text.lower()
        matches = sum(1 for kw in keywords if kw in t_lower)
        return matches >= min_matches

    lines = []
    total_chars = 0

    for cid in sorted(chunk_ids):
        chunk_file = chunks_dir / f"chunk_{cid:03d}.json"
        if not chunk_file.exists():
            continue

        chunk = json.loads(chunk_file.read_text(encoding="utf-8"))

        for session in chunk.get("sessions", []):
            msgs = session.get("messages", [])
            session_text = " ".join(m.get("memory", "") for m in msgs)
            if not is_relevant(session_text):
                continue

            # This session is relevant — extract messages
            sid = session["session_id"][:12]
            date = msgs[0].get("created_at", "")[:10] if msgs else "?"

            header = f"\n=== {date} | session {sid} | {len(msgs)} messages ==="
            lines.append(header)
            total_chars += len(header)

            # Smart sampling: first 5 + last 3, or all if ≤10
            if len(msgs) <= 10:
                sampled = msgs
            else:
                sampled = (
                    msgs[:5]
                    + [{"role": "system", "memory": f"... [{len(msgs) - 8} messages omitted] ..."}]
                    + msgs[-3:]
                )

            for msg in sampled:
                mem = msg.get("memory", "")
                if not mem:
                    continue
                role = msg.get("role", "?")
                label = "USER" if role == "user" else ("ASSISTANT" if role == "assistant" else "")
                entry = f"{label}: {mem[:500]}\n" if label else f"{mem[:500]}\n"

                if total_chars + len(entry) > max_chars:
                    lines.append("... [truncated] ...")
                    return "\n".join(lines)

                lines.append(entry)
                total_chars += len(entry)

    return "\n".join(lines)


def extract_thread_experience(
    thread: dict,
    chunks_dir: Path,
    output_dir: Path,
    force: bool = False,
    timeout: int = 300,
) -> Optional[dict]:
    """Extract structured experience from one thread.

    Args:
        thread: Thread dict from threads.json
        chunks_dir: Directory with chunk files
        output_dir: Where to save thread experience files
        force: Re-extract even if file exists
        timeout: Opus call timeout

    Returns:
        Parsed experience dict, or None on failure.
    """
    tid = thread["thread_id"]
    name = thread["name"]

    # Safe filename
    safe_name = "".join(
        c if c.isalnum() or c in "-_ " else "" for c in name
    )[:50].strip().replace(" ", "_")
    exp_file = output_dir / f"thread_{tid:03d}_{safe_name}.json"

    if exp_file.exists() and not force:
        logger.info("Thread %d: already extracted, skipping", tid)
        return json.loads(exp_file.read_text(encoding="utf-8"))

    # Gather raw messages
    thread_text = _gather_thread_messages(thread, chunks_dir)
    if not thread_text or len(thread_text) < 200:
        logger.warning("Thread %d: too little data (%d chars)", tid, len(thread_text))
        return None

    # Build prompt
    prompt = EXPERIENCE_EXTRACTION_PROMPT.format(
        thread_json=json.dumps(thread, indent=2, ensure_ascii=False),
    )
    full_prompt = f"{prompt}\n\n---\n\nRAW CONVERSATION DATA:\n\n{thread_text}"

    logger.info(
        "Thread %d (%s): extracting experience (%d chars of context)...",
        tid, name, len(thread_text),
    )

    response = _call_opus(full_prompt, timeout=timeout)

    try:
        experience = _parse_json(response)
        if experience:
            # Add thread metadata
            experience["thread_id"] = tid
            experience["thread_name"] = name

            with open(exp_file, "w", encoding="utf-8") as f:
                json.dump(experience, f, ensure_ascii=False, indent=2)

            n_exp = len(experience.get("experiences", []))
            n_events = len(experience.get("timeline", []))
            logger.info(
                "Thread %d: %d timeline events, %d experience labels",
                tid, n_events, n_exp,
            )
            return experience
    except (json.JSONDecodeError, TypeError) as e:
        logger.error("Thread %d: failed to parse experience: %s", tid, e)

    return None


def run_experience_extraction(
    chunks_dir: Optional[Path] = None,
    thread_ids: Optional[List[int]] = None,
    force: bool = False,
) -> Dict:
    """Run experience extraction for all (or specified) threads.

    Args:
        chunks_dir: Directory containing chunks and threads.json.
        thread_ids: If set, only process these thread IDs.
        force: Re-extract even if experience file exists.

    Returns summary dict.
    """
    if chunks_dir is None:
        from ..core.config import DATA_DIR
        chunks_dir = DATA_DIR / CHUNKS_DIR_NAME

    threads_file = chunks_dir / "threads.json"
    if not threads_file.exists():
        return {"error": "No threads.json found. Run thread grouping first."}

    threads = json.loads(threads_file.read_text(encoding="utf-8"))
    output_dir = chunks_dir / THREADS_DIR_NAME
    output_dir.mkdir(exist_ok=True)

    results = []
    for thread in threads:
        tid = thread["thread_id"]
        if thread_ids and tid not in thread_ids:
            continue

        experience = extract_thread_experience(
            thread, chunks_dir, output_dir, force=force,
        )

        if experience:
            results.append({
                "thread_id": tid,
                "name": thread["name"],
                "timeline_events": len(experience.get("timeline", [])),
                "experience_labels": len(experience.get("experiences", [])),
                "status": experience.get("summary", {}).get("status", "?"),
            })
        else:
            results.append({
                "thread_id": tid,
                "name": thread["name"],
                "status": "failed",
            })

    # Summary
    summary = {
        "total_threads": len(threads),
        "processed": len([r for r in results if r.get("experience_labels")]),
        "total_experiences": sum(r.get("experience_labels", 0) for r in results),
        "results": results,
    }

    summary_file = output_dir / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary

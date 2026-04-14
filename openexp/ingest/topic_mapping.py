"""Per-chunk topic extraction for Experience Library.

Pipeline step 2: For each chunk, LLM extracts distinct topics/projects/threads.
Uses claude -p (Max subscription) with Haiku for speed and cost (~$0.10/chunk).

Output per chunk: JSON with topics [{name, description, session_ids, message_count}].
"""
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

TOPIC_MODEL = os.getenv("OPENEXP_TOPIC_MODEL", "haiku")
CHUNKS_DIR_NAME = "chunks"

TOPIC_EXTRACTION_PROMPT = """\
You are analyzing a batch of work conversations between a user and their AI assistant.

Your job: identify ALL distinct TOPICS, PROJECTS, or WORK THREADS in this batch.

A topic is a distinct stream of work. Examples:
- "Acme CRM Integration" (client negotiations, proposal, pricing)
- "OpenExp v2 refactor" (code cleanup, architecture changes)
- "Widget Co analytics project" (email templates, analytics)
- "Daily briefing / task planning" (morning routines, prioritization)
- "Infrastructure migration" (server setup, DNS, deployment)

## Rules
1. Each topic must be a DISTINCT thread of work, not a single message
2. Include the topic name, a 1-2 sentence description, which session_ids it appears in, and approximate message count
3. Be specific: "Acme CRM integration proposal" not "client work"
4. Include ALL topics, even small ones (3+ messages)
5. If a topic spans business development (leads, proposals, negotiations) — note the stage and outcome if visible

## Output format
Return ONLY a JSON array:
```json
[
  {
    "name": "Topic Name",
    "description": "What this thread is about, key context",
    "session_ids": ["abc123", "def456"],
    "message_count": 42,
    "category": "business|technical|personal|planning",
    "outcome_hint": "deal closed $X" or "in progress" or "abandoned" or null
  }
]
```

Be thorough. Miss nothing. 10-30 topics per chunk is normal.
"""


def _format_chunk_for_llm(chunk: dict, max_chars: int = 50_000) -> str:
    """Format a chunk's messages for LLM consumption.

    Samples from beginning, middle, and end of each session to stay within
    max_chars while covering all topics. 50K chars ≈ 12K tokens — enough
    for Haiku to identify all topics without timeout issues.
    """
    sessions = chunk.get("sessions", [])
    if not sessions:
        return ""

    # Budget chars per session (equal split)
    chars_per_session = max(max_chars // max(len(sessions), 1), 2000)

    lines = []
    total_chars = 0

    for session in sessions:
        sid = session["session_id"]
        msgs = [m for m in session.get("messages", []) if m.get("memory")]
        if not msgs:
            continue

        header = f"\n=== SESSION {sid[:12]} ({len(msgs)} messages) ==="
        lines.append(header)
        total_chars += len(header)

        # Sample: first third + last third of messages (covers start and end of conversation)
        if len(msgs) <= 20:
            sampled = msgs
        else:
            n = max(len(msgs) // 3, 5)
            sampled = msgs[:n] + [{"role": "system", "memory": f"... [{len(msgs) - 2*n} messages omitted] ..."}] + msgs[-n:]

        session_chars = 0
        for msg in sampled:
            role = msg.get("role", "?")
            text = msg.get("memory", "")
            label = "USER" if role == "user" else ("ASSISTANT" if role == "assistant" else "")
            entry = f"{label}: {text}\n" if label else f"{text}\n"

            if session_chars + len(entry) > chars_per_session:
                lines.append("... [session truncated] ...")
                break
            if total_chars + len(entry) > max_chars:
                lines.append("... [chunk truncated] ...")
                return "\n".join(lines)

            lines.append(entry)
            total_chars += len(entry)
            session_chars += len(entry)

    return "\n".join(lines)


def _parse_json_response(response_text: str) -> Optional[list]:
    """Extract JSON array from LLM response (may be wrapped in markdown)."""
    if not response_text:
        return None
    json_text = response_text
    if "```json" in json_text:
        json_text = json_text.split("```json")[1].split("```")[0]
    elif "```" in json_text:
        json_text = json_text.split("```")[1].split("```")[0]
    items = json.loads(json_text.strip())
    if not isinstance(items, list):
        items = [items]
    return items


def _get_api_key() -> Optional[str]:
    """Load API key from env or .env file."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    # Try .env in openexp dir
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _extract_topics_api(chunk_text: str, chunk_id: int, api_key: str) -> List[dict]:
    """Extract topics using Anthropic API directly (faster for batch)."""
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed, falling back to claude -p")
        return []

    model_map = {"haiku": "claude-haiku-4-5-latest", "sonnet": "claude-sonnet-4-5-latest"}
    model_id = model_map.get(TOPIC_MODEL, TOPIC_MODEL)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model_id,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": (
                    f"{TOPIC_EXTRACTION_PROMPT}\n\n---\n\n"
                    f"Analyze this conversation batch (chunk {chunk_id}):\n\n"
                    f"{chunk_text}"
                ),
            }],
        )
        response_text = response.content[0].text
        items = _parse_json_response(response_text)
        if items:
            logger.info("Chunk %d: extracted %d topics (API, %s)", chunk_id, len(items), model_id)
        return items or []
    except json.JSONDecodeError as e:
        logger.error("Failed to parse API response for chunk %d: %s", chunk_id, e)
        return []
    except Exception as e:
        logger.error("API call failed for chunk %d: %s", chunk_id, e)
        return []


def _extract_topics_cli(chunk_text: str, chunk_id: int) -> List[dict]:
    """Extract topics using claude -p (Max subscription fallback)."""
    full_prompt = (
        f"{TOPIC_EXTRACTION_PROMPT}\n\n---\n\n"
        f"Analyze this conversation batch (chunk {chunk_id}):\n\n"
        f"{chunk_text}"
    )
    try:
        env = {**os.environ, "OPENEXP_EXTRACT_RUNNING": "1"}
        env.pop("ANTHROPIC_API_KEY", None)
        result = subprocess.run(
            ["claude", "-p", "--model", TOPIC_MODEL],
            input=full_prompt, capture_output=True, text=True,
            timeout=300, env=env,
        )
        if result.returncode != 0:
            logger.error("claude -p failed for chunk %d (exit=%d)", chunk_id, result.returncode)
            return []
        items = _parse_json_response(result.stdout.strip())
        if items:
            logger.info("Chunk %d: extracted %d topics (CLI)", chunk_id, len(items))
        return items or []
    except subprocess.TimeoutExpired:
        logger.error("claude -p timed out for chunk %d", chunk_id)
        return []
    except json.JSONDecodeError as e:
        logger.error("Failed to parse CLI response for chunk %d: %s", chunk_id, e)
        return []
    except Exception as e:
        logger.error("Topic extraction failed for chunk %d: %s", chunk_id, e)
        return []


def _extract_topics_llm(chunk_text: str, chunk_id: int) -> List[dict]:
    """Call LLM to extract topics. Tries API first, falls back to claude -p."""
    if not chunk_text or len(chunk_text) < 200:
        logger.info("Chunk %d too short for topic extraction (%d chars)", chunk_id, len(chunk_text))
        return []

    api_key = _get_api_key()
    if api_key:
        result = _extract_topics_api(chunk_text, chunk_id, api_key)
        if result:
            return result
        logger.warning("API extraction failed for chunk %d, trying CLI fallback", chunk_id)

    return _extract_topics_cli(chunk_text, chunk_id)


def run_topic_mapping(
    chunks_dir: Optional[Path] = None,
    chunk_ids: Optional[List[int]] = None,
    force: bool = False,
) -> Dict:
    """Run topic extraction on all (or specified) chunks.

    Args:
        chunks_dir: Directory containing chunk JSON files.
        chunk_ids: If set, only process these chunk IDs. Otherwise all.
        force: Re-extract even if topics file already exists.

    Returns summary dict.
    """
    if chunks_dir is None:
        from ..core.config import DATA_DIR
        chunks_dir = DATA_DIR / CHUNKS_DIR_NAME

    manifest_path = chunks_dir / "manifest.json"
    if not manifest_path.exists():
        return {"error": "No manifest.json found. Run 'openexp chunk' first."}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    results = []
    skipped = 0
    failed = 0

    for chunk_info in manifest["chunks"]:
        cid = chunk_info["chunk_id"]

        if chunk_ids and cid not in chunk_ids:
            continue

        topics_file = chunks_dir / f"chunk_{cid:03d}_topics.json"

        # Skip if already extracted (unless force)
        if topics_file.exists() and not force:
            logger.info("Chunk %d: topics already extracted, skipping", cid)
            skipped += 1
            existing = json.loads(topics_file.read_text(encoding="utf-8"))
            results.append({
                "chunk_id": cid,
                "topics_count": len(existing.get("topics", [])),
                "status": "skipped",
            })
            continue

        # Load chunk
        chunk_file = chunks_dir / chunk_info["file"]
        if not chunk_file.exists():
            logger.error("Chunk file not found: %s", chunk_file)
            failed += 1
            continue

        chunk = json.loads(chunk_file.read_text(encoding="utf-8"))
        chunk_text = _format_chunk_for_llm(chunk)

        logger.info("Chunk %d: extracting topics (%d chars, %d sessions)...",
                     cid, len(chunk_text), chunk_info["session_count"])

        topics = _extract_topics_llm(chunk_text, cid)

        if not topics:
            failed += 1
            results.append({"chunk_id": cid, "topics_count": 0, "status": "failed"})
            continue

        # Save topics
        output = {
            "chunk_id": cid,
            "date_range": chunk_info["date_range"],
            "session_count": chunk_info["session_count"],
            "total_tokens": chunk_info["total_tokens"],
            "topics": topics,
        }
        with open(topics_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        results.append({
            "chunk_id": cid,
            "topics_count": len(topics),
            "status": "extracted",
        })

    return {
        "total_chunks": len(manifest["chunks"]),
        "processed": len([r for r in results if r["status"] == "extracted"]),
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }

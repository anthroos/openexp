"""Extract decisions from Claude Code conversation transcripts.

Instead of recording "Edited X.html" (action), extracts:
- What was the choice point?
- What alternatives existed?
- Why was this path chosen?
- What was learned?

Uses claude -p (Max subscription, Opus 4.6) — extraction quality IS the product.
"""
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Configurable via env vars
# Opus 4.6 — quality of extraction determines quality of the entire memory system.
# This is not a place to save money. This is the annotation layer.
EXTRACT_MODEL = os.getenv("OPENEXP_EXTRACT_MODEL", "claude-opus-4-6")
# Max chars of transcript to send to LLM (cost control)
EXTRACT_CONTEXT_LIMIT = int(os.getenv("OPENEXP_EXTRACT_CONTEXT_LIMIT", "30000"))

EXTRACTION_PROMPT = """\
You are analyzing a work session between a user and their AI assistant.

Your job: extract DECISIONS and STRATEGIC INSIGHTS — not actions.

## What to extract

1. **DECISIONS** — moments where a choice was made.
   - What was the choice point?
   - What was chosen and why?
   - What was the alternative?

2. **INSIGHTS** — things learned about clients, markets, patterns.
   - What was the insight?
   - Why does it matter for future work?

3. **COMMITMENTS** — promises or agreements made.
   - Who committed to what, by when?

## What NOT to extract
- File edits, tool calls, code changes (already captured separately)
- Calendar scheduling, meeting logistics
- Greetings, acknowledgments, filler
- Technical implementation details (code structure, config changes)

## Output format
Return a JSON array. Each item:
```json
{
  "type": "decision" | "insight" | "commitment",
  "content": "One clear sentence describing what happened and WHY",
  "importance": 0.0-1.0,
  "tags": ["client-name", "domain"],
  "client_id": "comp-xxx or null"
}
```

Be selective. 3-8 items per session is ideal. Only extract what would be valuable
to recall in a FUTURE conversation — the kind of context that changes how you
approach the next similar situation.

Think strategically: helicopter view + details. Not "sent email" but "chose to
lead with social proof because enterprise clients trust references".
"""


def read_transcript(transcript_path: Path, session_id: Optional[str] = None) -> str:
    """Read and format a Claude Code transcript for LLM extraction.

    Returns a condensed text of user<>assistant exchanges,
    skipping tool results, system messages, and other noise.
    """
    if not transcript_path.exists():
        return ""

    messages = []
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = entry.get("type")
        if msg_type not in ("user", "assistant"):
            continue

        # Skip tool results (user messages that are just tool output)
        if msg_type == "user":
            content = entry.get("message", {}).get("content", [])
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    # Skip hook injections and system reminders
                    if text and not text.startswith("<system-reminder>"):
                        texts.append(text)
            if not texts:
                continue
            messages.append(("user", "\n".join(texts)))

        elif msg_type == "assistant":
            content = entry.get("message", {}).get("content", [])
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        texts.append(text)
            if not texts:
                continue
            messages.append(("assistant", "\n".join(texts)))

    if not messages:
        return ""

    # Build condensed transcript, respecting context limit
    # Prioritize recent messages (most likely to contain decisions)
    formatted = []
    total_chars = 0
    for role, text in reversed(messages):
        entry_text = f"{'IVAN' if role == 'user' else 'ASSISTANT'}: {text}\n"
        if total_chars + len(entry_text) > EXTRACT_CONTEXT_LIMIT:
            break
        formatted.append(entry_text)
        total_chars += len(entry_text)

    formatted.reverse()
    return "\n".join(formatted)


def extract_decisions(
    transcript_text: str,
    session_id: str = "",
    experience: str = "default",
) -> List[Dict]:
    """Extract decisions from a transcript using claude -p (Max subscription).

    Uses Claude Code CLI in pipe mode to leverage the user's Max subscription
    instead of requiring API credits. --verbose flag suppresses hooks to avoid
    recursion (this runs inside SessionEnd hook).

    Returns list of extracted items (decisions, insights, commitments).
    """
    if not transcript_text or len(transcript_text) < 100:
        logger.info("Transcript too short for extraction (%d chars)", len(transcript_text))
        return []

    # Build the full prompt: system instructions + transcript
    full_prompt = (
        f"{EXTRACTION_PROMPT}\n\n"
        f"---\n\n"
        f"Extract decisions and insights from this work session:\n\n"
        f"{transcript_text}"
    )

    response_text = ""
    try:
        # Use claude -p (pipe mode) with Max subscription
        # --model opus: use Opus 4.6 for highest extraction quality
        # OPENEXP_EXTRACT_RUNNING=1 prevents hook recursion (session-end checks this)
        env = {**os.environ, "OPENEXP_EXTRACT_RUNNING": "1"}
        # Remove ANTHROPIC_API_KEY so claude -p uses Max subscription, not API credits
        env.pop("ANTHROPIC_API_KEY", None)
        result = subprocess.run(
            ["claude", "-p", "--model", "opus"],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=120,  # 2 min timeout for Opus
            env=env,
        )

        if result.returncode != 0:
            logger.error(
                "claude -p failed (exit=%d): %s",
                result.returncode, result.stderr[:500],
            )
            return []

        response_text = result.stdout.strip()
        if not response_text:
            logger.error("claude -p returned empty response")
            return []

        # Extract JSON from response (may be wrapped in markdown code block)
        json_text = response_text
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0]
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0]

        items = json.loads(json_text.strip())
        if not isinstance(items, list):
            items = [items]

        logger.info(
            "Extracted %d items from transcript (%d chars, model=%s, via claude -p)",
            len(items), len(transcript_text), EXTRACT_MODEL,
        )
        return items

    except subprocess.TimeoutExpired:
        logger.error("claude -p timed out after 120s")
        return []
    except json.JSONDecodeError as e:
        logger.error("Failed to parse extraction response: %s", e)
        logger.debug("Response was: %s", response_text[:500] if response_text else "empty")
        return []
    except FileNotFoundError:
        logger.error("claude CLI not found in PATH — is Claude Code installed?")
        return []
    except Exception as e:
        logger.error("Decision extraction failed: %s", e)
        return []


def extract_and_store(
    transcript_path: Path,
    session_id: str,
    experience: str = "default",
    dry_run: bool = False,
) -> Dict:
    """Full pipeline: read transcript → extract → store as memories.

    Returns summary of what was extracted and stored.
    """
    transcript_text = read_transcript(transcript_path, session_id)
    if not transcript_text:
        return {"extracted": 0, "reason": "empty_transcript"}

    items = extract_decisions(transcript_text, session_id, experience)
    if not items:
        return {"extracted": 0, "reason": "no_decisions_found"}

    if dry_run:
        return {"extracted": len(items), "items": items, "dry_run": True}

    # Store each item as a memory via the openexp API
    stored = 0
    from ..core.config import COLLECTION_NAME
    from ..core.direct_search import _embed, _get_qdrant
    from qdrant_client.models import PointStruct
    import uuid
    from datetime import datetime, timezone

    client = _get_qdrant()

    for item in items:
        content = item.get("content", "")
        if not content:
            continue

        item_type = item.get("type", "decision")
        importance = item.get("importance", 0.5)
        tags = item.get("tags", [])
        client_id = item.get("client_id")

        memory_type = {
            "decision": "decision",
            "insight": "insight",
            "commitment": "action",
        }.get(item_type, "decision")

        try:
            vector = _embed(content)
            point_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()

            payload = {
                "memory": content,
                "type": memory_type,
                "agent": "session",
                "source": "decision_extraction",
                "importance": importance,
                "tags": tags,
                "session_id": session_id,
                "experience": experience,
                "created_at": now,
                "status": "active",
            }
            if client_id:
                payload["client_id"] = client_id

            client.upsert(
                collection_name=COLLECTION_NAME,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                ],
            )
            stored += 1
            logger.info("Stored decision: %s (type=%s, importance=%.1f)", content[:80], memory_type, importance)

        except Exception as e:
            logger.error("Failed to store decision '%s': %s", content[:50], e)

    return {
        "extracted": len(items),
        "stored": stored,
        "experience": experience,
        "model": EXTRACT_MODEL,
    }

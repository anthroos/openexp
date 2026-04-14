"""Chunk all transcript data into ~200K token batches for experience extraction.

Pipeline step 1: Read all transcript points from Qdrant → group by session →
sort chronologically → split into chunks that fit in an LLM context window.

Each chunk is a self-contained batch of conversations, never splitting a session
across chunks (unless a single session exceeds the token limit).
"""
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from ..core.config import COLLECTION_NAME, QDRANT_HOST, QDRANT_PORT

logger = logging.getLogger(__name__)

# ~200K tokens ≈ 800K chars (1 token ≈ 4 chars)
DEFAULT_CHUNK_SIZE_CHARS = 800_000
CHUNKS_DIR_NAME = "chunks"


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _fetch_all_transcripts(client: QdrantClient) -> List[dict]:
    """Fetch all transcript points from Qdrant with key payload fields."""
    all_points = []
    offset = None
    for _ in range(500):  # safety limit
        pts, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=250,
            offset=offset,
            with_payload=["memory", "session_id", "created_at", "role"],
            with_vectors=False,
            scroll_filter=Filter(
                must=[FieldCondition(key="source", match=MatchValue(value="transcript"))]
            ),
        )
        for p in pts:
            all_points.append({
                "id": str(p.id),
                "memory": p.payload.get("memory", ""),
                "session_id": p.payload.get("session_id", "unknown"),
                "created_at": p.payload.get("created_at", ""),
                "role": p.payload.get("role", "unknown"),
            })
        if offset is None:
            break
    return all_points


def _group_by_session(points: List[dict]) -> Dict[str, List[dict]]:
    """Group points by session_id, sort each session by created_at."""
    sessions = defaultdict(list)
    for p in points:
        sessions[p["session_id"]].append(p)
    # Sort messages within each session
    for msgs in sessions.values():
        msgs.sort(key=lambda m: m.get("created_at", ""))
    return dict(sessions)


def _sort_sessions_chronologically(sessions: Dict[str, List[dict]]) -> List[str]:
    """Return session_ids sorted by their earliest message timestamp."""
    session_start = {}
    for sid, msgs in sessions.items():
        dates = [m["created_at"] for m in msgs if m["created_at"]]
        session_start[sid] = min(dates) if dates else ""
    return sorted(sessions.keys(), key=lambda sid: session_start.get(sid, ""))


def _session_char_count(messages: List[dict]) -> int:
    return sum(len(m["memory"]) for m in messages)


def _split_large_session(messages: List[dict], max_chars: int) -> List[List[dict]]:
    """Split a session that exceeds max_chars into sub-chunks."""
    sub_chunks = []
    current = []
    current_size = 0
    for msg in messages:
        msg_size = len(msg["memory"])
        if current and current_size + msg_size > max_chars:
            sub_chunks.append(current)
            current = []
            current_size = 0
        current.append(msg)
        current_size += msg_size
    if current:
        sub_chunks.append(current)
    return sub_chunks


def build_chunks(
    sessions: Dict[str, List[dict]],
    sorted_session_ids: List[str],
    max_chunk_chars: int = DEFAULT_CHUNK_SIZE_CHARS,
) -> List[dict]:
    """Pack sessions into chunks, respecting max size.

    Returns list of chunk dicts:
    {
        "chunk_id": 1,
        "sessions": [{"session_id": "...", "messages": [...]}],
        "total_chars": int,
        "total_tokens": int,
        "total_messages": int,
        "date_range": {"start": "...", "end": "..."},
    }
    """
    chunks = []
    current_sessions = []
    current_chars = 0

    def _finalize_chunk():
        if not current_sessions:
            return
        all_dates = []
        total_msgs = 0
        for s in current_sessions:
            total_msgs += len(s["messages"])
            for m in s["messages"]:
                if m.get("created_at"):
                    all_dates.append(m["created_at"])
        chunks.append({
            "chunk_id": len(chunks) + 1,
            "sessions": current_sessions,
            "session_count": len(current_sessions),
            "total_chars": current_chars,
            "total_tokens": current_chars // 4,
            "total_messages": total_msgs,
            "date_range": {
                "start": min(all_dates) if all_dates else "",
                "end": max(all_dates) if all_dates else "",
            },
        })

    for sid in sorted_session_ids:
        msgs = sessions[sid]
        session_chars = _session_char_count(msgs)

        # Large session: split into sub-chunks
        if session_chars > max_chunk_chars:
            # Finalize current chunk first
            _finalize_chunk()
            current_sessions = []
            current_chars = 0

            sub_chunks = _split_large_session(msgs, max_chunk_chars)
            for i, sub in enumerate(sub_chunks):
                sub_sid = f"{sid}__part{i+1}"
                current_sessions = [{"session_id": sub_sid, "messages": sub}]
                current_chars = _session_char_count(sub)
                _finalize_chunk()
                current_sessions = []
                current_chars = 0
            continue

        # Would this session overflow the current chunk?
        if current_chars + session_chars > max_chunk_chars and current_sessions:
            _finalize_chunk()
            current_sessions = []
            current_chars = 0

        current_sessions.append({"session_id": sid, "messages": msgs})
        current_chars += session_chars

    # Don't forget the last chunk
    _finalize_chunk()
    return chunks


def run_chunking(
    output_dir: Optional[Path] = None,
    max_chunk_chars: int = DEFAULT_CHUNK_SIZE_CHARS,
) -> Dict:
    """Run the full chunking pipeline.

    Returns summary dict with chunk stats.
    """
    if output_dir is None:
        from ..core.config import DATA_DIR
        output_dir = DATA_DIR / CHUNKS_DIR_NAME

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Connecting to Qdrant...")
    client = QdrantClient(url=f"http://{QDRANT_HOST}:{QDRANT_PORT}", timeout=30)

    logger.info("Fetching all transcript points...")
    points = _fetch_all_transcripts(client)
    logger.info("Fetched %d transcript points", len(points))

    sessions = _group_by_session(points)
    sorted_ids = _sort_sessions_chronologically(sessions)
    logger.info("Found %d sessions", len(sessions))

    chunks = build_chunks(sessions, sorted_ids, max_chunk_chars)
    logger.info("Built %d chunks", len(chunks))

    # Write chunks to disk
    manifest = []
    for chunk in chunks:
        chunk_file = output_dir / f"chunk_{chunk['chunk_id']:03d}.json"
        with open(chunk_file, "w", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=2, default=str)

        manifest.append({
            "chunk_id": chunk["chunk_id"],
            "file": chunk_file.name,
            "session_count": chunk["session_count"],
            "total_tokens": chunk["total_tokens"],
            "total_messages": chunk["total_messages"],
            "date_range": chunk["date_range"],
        })

    # Write manifest
    manifest_file = output_dir / "manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump({
            "total_chunks": len(chunks),
            "total_points": len(points),
            "total_sessions": len(sessions),
            "max_chunk_chars": max_chunk_chars,
            "chunks": manifest,
        }, f, ensure_ascii=False, indent=2)

    return {
        "total_chunks": len(chunks),
        "total_points": len(points),
        "total_sessions": len(sessions),
        "chunks": manifest,
        "output_dir": str(output_dir),
    }

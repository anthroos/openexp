"""Distill — Layer 2: re-runnable compression of raw transcripts into Qdrant.

Reads ONLY from the raw store (Layer 1, raw_store.py) — never from live
Claude Code transcripts. Every point written is stamped with
DISTILL_VERSION; improving the compressor means bumping the version and
re-running `openexp distill --all`, which replaces each session's stale
points. Distill never writes to raw.
"""
import fcntl
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from qdrant_client.models import Filter, FieldCondition, FilterSelector, MatchAny, MatchValue

from ..core.config import COLLECTION_NAME, RAW_DIR
from ..core.direct_search import _get_qdrant
from .extract_decisions import extract_and_store
from .transcript import ingest_transcript

logger = logging.getLogger(__name__)

# Bump when the compression logic changes (extraction prompt, truncation,
# filtering, payload shape). Sessions distilled with an older version are
# re-distilled on the next run.
# v2 (2026-07-03): within-session dedup, MIN_MESSAGE_CHARS 10→20,
#   full-transcript chunked decision extraction (was: last 30K chars only).
DISTILL_VERSION = 2

# Sources this pipeline owns in Qdrant. Anything else (add_memory,
# observations, packs) is never touched by distill.
_TRANSCRIPT_SOURCE = "transcript"
_DECISION_SOURCE = "decision_extraction"


def _session_filter(session_id: str, sources: List[str]) -> Filter:
    return Filter(must=[
        FieldCondition(key="session_id", match=MatchValue(value=session_id)),
        FieldCondition(key="source", match=MatchAny(any=sources)),
    ])


def _session_distill_version(client, session_id: str) -> Optional[int]:
    """Version a session was last distilled with.

    Returns None if the session has no distilled points at all, and 0 for
    points written by the pre-distill flow (no version stamp).
    """
    try:
        points, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=_session_filter(session_id, [_TRANSCRIPT_SOURCE]),
            limit=1,
            with_payload=["distill_version"],
            with_vectors=False,
        )
    except Exception as e:
        logger.warning("Failed to check distill version for %s: %s", session_id[:8], e)
        return None
    if not points:
        return None
    return int(points[0].payload.get("distill_version") or 0)


def _delete_session_points(client, session_id: str, sources: List[str]) -> None:
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(filter=_session_filter(session_id, sources)),
    )


def distill_session(
    session_id: str,
    raw_dir: Optional[Path] = None,
    experience: str = "default",
    extract: bool = True,
    dry_run: bool = False,
    force: bool = False,
) -> Dict:
    """Distill one session from the raw store into Qdrant.

    Idempotent by (session_id, DISTILL_VERSION): a session already at the
    current version is skipped unless force. An older version means its
    points are deleted and rewritten. With extract=False only transcript
    points are replaced — existing extracted decisions are kept (they cost
    LLM calls to produce).

    Concurrency-safe: holds an exclusive flock on the raw file; a session
    being distilled by another process returns {"status": "locked"}.
    """
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    raw_path = raw_dir / f"{session_id}.jsonl"
    if not raw_path.is_file():
        return {"status": "no_raw", "session_id": session_id}

    # Per-session lock on the raw file: two distill instances racing on the
    # same session (e.g. nightly job + manual --all, 2026-07-03 incident)
    # interleave delete+rewrite and duplicate points. Loser skips.
    lock_fd = open(raw_path, "rb")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fd.close()
        logger.info("Session %s locked by another distill, skipping", session_id[:8])
        return {"status": "locked", "session_id": session_id}
    try:
        return _distill_session_locked(
            session_id, raw_path, experience, extract, dry_run, force,
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _distill_session_locked(
    session_id: str,
    raw_path: Path,
    experience: str,
    extract: bool,
    dry_run: bool,
    force: bool,
) -> Dict:
    client = _get_qdrant()
    existing = _session_distill_version(client, session_id)
    if not force and existing is not None and existing >= DISTILL_VERSION:
        return {"status": "current", "session_id": session_id, "distill_version": existing}

    if dry_run:
        return {
            "status": "would_distill",
            "session_id": session_id,
            "existing_version": existing,
            "distill_version": DISTILL_VERSION,
        }

    if existing is not None:
        sources = [_TRANSCRIPT_SOURCE, _DECISION_SOURCE] if extract else [_TRANSCRIPT_SOURCE]
        _delete_session_points(client, session_id, sources)
        logger.info("Deleted stale points (v%s) for session %s", existing, session_id[:8])

    result = {
        "status": "distilled",
        "session_id": session_id,
        "distill_version": DISTILL_VERSION,
    }
    if extract:
        result["decisions"] = extract_and_store(
            transcript_path=raw_path,
            session_id=session_id,
            experience=experience,
            distill_version=DISTILL_VERSION,
        )
    result["transcript"] = ingest_transcript(
        transcript_path=raw_path,
        session_id=session_id,
        experience=experience,
        force=True,  # idempotency is handled at the distill level
        distill_version=DISTILL_VERSION,
    )
    return result


def distill_all(
    raw_dir: Optional[Path] = None,
    experience: str = "default",
    since: Optional[datetime] = None,
    extract: bool = True,
    dry_run: bool = False,
    force: bool = False,
    progress: bool = False,
) -> Dict:
    """Distill every session in the raw store (optionally only files
    modified since a date). Sessions already at DISTILL_VERSION are skipped.
    """
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    stats = {"found": 0, "distilled": 0, "current": 0, "would_distill": 0, "locked": 0, "errors": 0}
    if not raw_dir.is_dir():
        logger.warning("Raw store not found: %s", raw_dir)
        return stats

    transcripts = sorted(raw_dir.glob("*.jsonl"))
    if since is not None:
        since_ts = since.replace(tzinfo=since.tzinfo or timezone.utc).timestamp()
        transcripts = [t for t in transcripts if t.stat().st_mtime >= since_ts]

    for i, t in enumerate(transcripts, 1):
        stats["found"] += 1
        if progress:
            print(f"\r  [{i}/{len(transcripts)}] {t.stem[:8]}...", end="", flush=True)
        try:
            r = distill_session(
                t.stem, raw_dir=raw_dir, experience=experience,
                extract=extract, dry_run=dry_run, force=force,
            )
            key = r["status"] if r["status"] in stats else "errors"
            stats[key] += 1
        except Exception as e:
            logger.error("Failed to distill %s: %s", t.stem[:8], e)
            stats["errors"] += 1
    if progress and transcripts:
        print()
    return stats

"""Raw transcript capture — Layer 1 of the memory pipeline.

Copies Claude Code transcripts AS-IS into a store OpenExp owns
(~/.openexp/raw/). No parsing, no filtering, no truncation — capture
never thinks. Claude Code rotates ~/.claude/projects after ~30 days;
this store is the permanent source of truth that distill re-reads.

Layout mirrors the source project dir:
    <raw_dir>/<session_id>.jsonl                       main transcript
    <raw_dir>/<session_id>/subagents/agent-*.jsonl     subagent transcripts
"""
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Optional

from ..core.config import RAW_DIR

logger = logging.getLogger(__name__)


def _copy_atomic(src: Path, dest: Path) -> bool:
    """Byte-exact copy of src to dest via temp file + rename.

    Idempotent: skips when dest is already at least as large as src
    (transcripts are append-only, so a same-or-larger copy is never staler).
    Returns True if a copy was made, False if skipped.
    """
    src_size = src.stat().st_size
    if dest.exists() and dest.stat().st_size >= src_size:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.stem}.", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp_name)
        os.replace(tmp_name, dest)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return True


def archive_transcript(
    transcript_path: Path,
    session_id: str,
    raw_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Archive a session's transcript (and its subagent transcripts) as-is.

    Returns the archived main transcript path, or None if the source
    doesn't exist. Never raises on subagent copy failures — the main
    transcript is what matters.
    """
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    transcript_path = Path(transcript_path)
    if not transcript_path.is_file():
        logger.warning("Transcript not found, nothing to archive: %s", transcript_path)
        return None

    dest = raw_dir / f"{session_id}.jsonl"
    if _copy_atomic(transcript_path, dest):
        logger.info("Archived raw transcript: %s (%d bytes)", dest.name, dest.stat().st_size)

    # Subagent transcripts live next to the main file: <session_id>/subagents/*.jsonl
    subagents_dir = transcript_path.parent / session_id / "subagents"
    if subagents_dir.is_dir():
        for agent_file in sorted(subagents_dir.glob("*.jsonl")):
            try:
                _copy_atomic(agent_file, raw_dir / session_id / "subagents" / agent_file.name)
            except OSError as e:
                logger.error("Failed to archive subagent transcript %s: %s", agent_file.name, e)

    return dest


def read_raw(
    session_id: str,
    raw_dir: Optional[Path] = None,
    grep: Optional[str] = None,
    context: int = 2,
) -> Dict:
    """Deep-recall read of an archived transcript (Layer 3, read-only).

    Without grep, returns every line. With grep, returns case-insensitive
    matching JSONL lines plus `context` lines around each match, in file
    order without duplicates. Lines are returned verbatim — interpreting
    them is the caller's job.
    """
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    path = raw_dir / f"{session_id}.jsonl"
    if not path.is_file():
        return {"session_id": session_id, "found": False, "lines": []}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if grep is None:
        selected = range(len(lines))
    else:
        needle = grep.lower()
        keep = set()
        for i, line in enumerate(lines):
            if needle in line.lower():
                keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
        selected = sorted(keep)

    return {
        "session_id": session_id,
        "found": True,
        "path": str(path),
        "total_lines": len(lines),
        "lines": [lines[i] for i in selected],
    }


def backfill(
    projects_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Archive every live transcript under the Claude projects dir.

    Mirrors each file's path relative to its project dir, so main and
    subagent transcripts land in the same layout archive_transcript uses.
    Re-runnable: already-archived files are skipped.
    """
    projects_dir = Path(projects_dir) if projects_dir else Path.home() / ".claude" / "projects"
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    stats = {"found": 0, "archived": 0, "skipped": 0, "errors": 0}
    if not projects_dir.is_dir():
        logger.warning("Projects dir not found: %s", projects_dir)
        return stats

    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        for src in sorted(project_dir.rglob("*.jsonl")):
            stats["found"] += 1
            if dry_run:
                continue
            try:
                copied = _copy_atomic(src, raw_dir / src.relative_to(project_dir))
                stats["archived" if copied else "skipped"] += 1
            except OSError as e:
                logger.error("Failed to archive %s: %s", src, e)
                stats["errors"] += 1

    return stats

"""Idempotency tracker for observation/session ingestion."""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


class IngestWatermark:
    """Tracks processed observation IDs and session filenames for idempotency."""

    def __init__(self, path: Path):
        self.path = path
        self.processed_obs: Set[str] = set()
        self.processed_sessions: Set[str] = set()
        self.stats = {"total_ingested": 0, "total_skipped": 0}
        self.last_run: Optional[str] = None
        self._new_obs_ids: Set[str] = set()
        self._new_sessions: Set[str] = set()
        self._stats_delta: Dict[str, int] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.processed_obs = set(data.get("processed_obs_ids", []))
                self.processed_sessions = set(data.get("processed_sessions", []))
                self.stats = data.get("stats", self.stats)
                self.last_run = data.get("last_run")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load watermark, starting fresh: %s", e)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "processed_obs_ids": list(self.processed_obs),
            "processed_sessions": list(self.processed_sessions),
            "last_run": datetime.now(timezone.utc).isoformat(),
            "stats": self.stats,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False))

    def is_obs_processed(self, obs_id: str) -> bool:
        return obs_id in self.processed_obs

    def mark_obs_processed(self, obs_id: str, ingested: bool = True):
        self.processed_obs.add(obs_id)
        self._new_obs_ids.add(obs_id)
        if ingested:
            self.stats["total_ingested"] = self.stats.get("total_ingested", 0) + 1

    def mark_obs_skipped(self):
        self.stats["total_skipped"] = self.stats.get("total_skipped", 0) + 1

    def is_session_processed(self, filename: str) -> bool:
        return filename in self.processed_sessions

    def mark_session_processed(self, filename: str):
        self.processed_sessions.add(filename)
        self._new_sessions.add(filename)

    def compact(self, max_age_days: int = 30):
        """Remove old observation IDs to keep watermark small."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cutoff_str = cutoff.strftime("%Y%m%d")
        before = len(self.processed_obs)
        self.processed_obs = {
            oid for oid in self.processed_obs
            if not oid.startswith("obs-") or oid.split("-")[1] >= cutoff_str
        }
        removed = before - len(self.processed_obs)
        if removed > 0:
            logger.info("Compacted watermark: removed %d old observation IDs", removed)

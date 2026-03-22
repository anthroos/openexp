"""Reward Tracker — tracks agent predictions and their outcomes.

When outcomes resolve, updates Q-values for memories that were used
in the prediction. This closes the learning loop.
"""
import fcntl
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.q_value import QValueUpdater, QCache

logger = logging.getLogger(__name__)

CAUSE_CATEGORIES = {
    "execution_failure",
    "strategy_failure",
    "qualification_failure",
    "hypothesis_failure",
    "external",
    "competition",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, data: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


class RewardTracker:
    """Tracks predictions -> outcomes -> Q-value updates."""

    def __init__(
        self,
        data_dir: Path,
        q_updater: Optional[QValueUpdater] = None,
        q_cache: Optional[QCache] = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.predictions_file = self.data_dir / "predictions.jsonl"
        self.outcomes_file = self.data_dir / "outcomes.jsonl"

        self.q_cache = q_cache or QCache()
        self.q_updater = q_updater or QValueUpdater(cache=self.q_cache)

        self._predictions: List[dict] = _load_jsonl(self.predictions_file)
        self._lock = threading.Lock()

    def log_prediction(
        self,
        prediction: str,
        confidence: float,
        strategic_value: float,
        memory_ids_used: List[str],
        client_id: Optional[str] = None,
    ) -> str:
        """Log an agent prediction for later resolution. Returns prediction ID."""
        pred_id = f"pred_{uuid.uuid4().hex[:8]}"

        entry = {
            "id": pred_id,
            "timestamp": _now_iso(),
            "prediction": prediction,
            "confidence": confidence,
            "strategic_value": strategic_value,
            "memory_ids_used": memory_ids_used,
            "client_id": client_id,
            "status": "pending",
        }

        with self._lock:
            _append_jsonl(self.predictions_file, entry)
            self._predictions.append(entry)

        logger.info("Logged prediction %s: %s (confidence=%.2f)", pred_id, prediction[:80], confidence)
        return pred_id

    def log_outcome(
        self,
        prediction_id: str,
        outcome: str,
        reward: float,
        source: str = "human",
        cause_category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Log an outcome for a prediction and update Q-values."""
        if cause_category and cause_category not in CAUSE_CATEGORIES:
            logger.warning("Unknown cause_category: %s", cause_category)

        with self._lock:
            pred = None
            for p in self._predictions:
                if p["id"] == prediction_id:
                    pred = p
                    break

            if pred is None:
                return {"error": f"Prediction {prediction_id} not found"}

            if pred["status"] != "pending":
                return {"error": f"Prediction {prediction_id} already resolved"}

            outcome_entry = {
                "prediction_id": prediction_id,
                "timestamp": _now_iso(),
                "outcome": outcome,
                "reward": reward,
                "source": source,
                "cause_category": cause_category,
            }
            _append_jsonl(self.outcomes_file, outcome_entry)

            pred["status"] = "resolved"
            pred["resolved_at"] = _now_iso()
            self._rewrite_predictions_file()

        # Update Q-values
        updated_q = {}
        memory_ids = pred.get("memory_ids_used", [])
        for mem_id in memory_ids:
            updated_q[mem_id] = self.q_updater.update(mem_id, reward, layer="action")

        logger.info(
            "Outcome for %s: reward=%.2f, updated %d memories",
            prediction_id, reward, len(updated_q),
        )

        return {
            "prediction_id": prediction_id,
            "reward": reward,
            "cause_category": cause_category,
            "memories_updated": len(updated_q),
            "q_updates": {k: v.get("q_value", 0) for k, v in updated_q.items()},
        }

    def get_pending_predictions(self, client_id: Optional[str] = None) -> List[dict]:
        """Get all unresolved predictions."""
        with self._lock:
            pending = [p for p in self._predictions if p["status"] == "pending"]
        if client_id:
            pending = [p for p in pending if p.get("client_id") == client_id]
        return pending

    def get_prediction_stats(self) -> Dict[str, Any]:
        """Get statistics on prediction accuracy."""
        with self._lock:
            predictions = list(self._predictions)
        outcomes = _load_jsonl(self.outcomes_file)

        total = len(predictions)
        resolved = len([p for p in predictions if p["status"] == "resolved"])
        pending = total - resolved

        if not outcomes:
            return {"total": total, "resolved": resolved, "pending": pending}

        positive = sum(1 for o in outcomes if o["reward"] > 0)
        negative = sum(1 for o in outcomes if o["reward"] < 0)
        avg_reward = sum(o["reward"] for o in outcomes) / len(outcomes)

        return {
            "total": total,
            "resolved": resolved,
            "pending": pending,
            "positive_outcomes": positive,
            "negative_outcomes": negative,
            "avg_reward": round(avg_reward, 3),
            "accuracy": round(positive / resolved, 3) if resolved > 0 else 0,
        }

    def _rewrite_predictions_file(self):
        """Rewrite predictions file from in-memory cache. Must be called under lock."""
        fd = open(self.predictions_file, "w", encoding="utf-8")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            for p in self._predictions:
                fd.write(json.dumps(p, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

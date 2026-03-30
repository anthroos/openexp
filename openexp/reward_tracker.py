"""Reward Tracker — tracks agent predictions and their outcomes.

When outcomes resolve, updates Q-values for memories that were used
in the prediction. This closes the learning loop.
"""
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.explanation import generate_reward_explanation, _fetch_memory_contents
from .core.q_value import QValueUpdater, QCache, compute_layer_rewards
from .core.reward_log import generate_reward_id, log_reward_event

logger = logging.getLogger(__name__)

def _build_prediction_reward_context(
    prediction: str, outcome: str, reward: float, cause_category: str | None = None,
) -> str:
    """Build a human-readable reward context for a prediction→outcome resolution.

    Format: "Pred +0.80: 'prediction snippet' -> 'outcome snippet'"
    """
    sign = "+" if reward >= 0 else ""
    pred_snippet = prediction[:40].replace("'", "")
    out_snippet = outcome[:40].replace("'", "")
    ctx = f"Pred {sign}{reward:.2f}: '{pred_snippet}' -> '{out_snippet}'"
    if cause_category:
        ctx += f" [{cause_category}]"
    return ctx


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


MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def _load_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        return []
    if file_size > MAX_FILE_SIZE:
        logger.warning("JSONL file too large, skipping: %s (%d bytes > %d limit)", path, file_size, MAX_FILE_SIZE)
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning("Skipping malformed JSONL line in %s: %s", path, e)
    return items


class RewardTracker:
    """Tracks predictions -> outcomes -> Q-value updates."""

    def __init__(
        self,
        data_dir: Path,
        q_updater: Optional[QValueUpdater] = None,
        q_cache: Optional[QCache] = None,
        experience: str = "default",
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.predictions_file = self.data_dir / "predictions.jsonl"
        self.outcomes_file = self.data_dir / "outcomes.jsonl"
        self.experience = experience

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
            memory_ids = list(pred.get("memory_ids_used", []))
            self._rewrite_predictions_file()

        # Update Q-values (outside lock — memory_ids copied inside lock)
        reward_ctx = _build_prediction_reward_context(
            pred.get("prediction", ""), outcome, reward, cause_category,
        )

        # L3 cold storage
        rwd_id = generate_reward_id()
        cold_context = {
            "prediction_id": prediction_id,
            "prediction": pred.get("prediction", ""),
            "outcome": outcome,
            "confidence": pred.get("confidence"),
            "strategic_value": pred.get("strategic_value"),
            "cause_category": cause_category,
            "source": source,
            "client_id": pred.get("client_id"),
        }

        # L4: read first memory's Q before update
        q_before = None
        if memory_ids:
            first_q_data = self.q_cache.get(memory_ids[0], self.experience)
            q_before = first_q_data.get("q_value", 0.0) if first_q_data else None

        updated_q = {}
        layer_rewards = compute_layer_rewards(reward)
        for mem_id in memory_ids:
            updated_q[mem_id] = self.q_updater.update_all_layers(
                mem_id, layer_rewards, experience=self.experience,
                reward_context=reward_ctx, reward_id=rwd_id,
            )

        # L4: read first memory's Q after update
        q_after = None
        if memory_ids:
            first_q_after = self.q_cache.get(memory_ids[0], self.experience)
            q_after = first_q_after.get("q_value", 0.0) if first_q_after else None

        # L4: generate explanation with q_before/q_after
        explanation = generate_reward_explanation(
            reward_type="prediction",
            reward=reward,
            context=cold_context,
            memory_contents=_fetch_memory_contents(memory_ids[:5]),
            q_before=q_before,
            q_after=q_after,
            experience=self.experience,
        )

        log_reward_event(
            reward_id=rwd_id,
            reward_type="prediction",
            reward=reward,
            memory_ids=memory_ids,
            context=cold_context,
            experience=self.experience,
            explanation=explanation,
        )

        logger.info(
            "Outcome for %s: reward=%.2f, updated %d memories (reward_id=%s)",
            prediction_id, reward, len(updated_q), rwd_id,
        )

        return {
            "prediction_id": prediction_id,
            "reward": reward,
            "reward_id": rwd_id,
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
        """Atomically rewrite predictions file from in-memory cache."""
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self.predictions_file.parent),
            prefix=".predictions_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                for p in self._predictions:
                    f.write(json.dumps(p, ensure_ascii=False) + "\n")
            os.replace(tmp_path, str(self.predictions_file))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

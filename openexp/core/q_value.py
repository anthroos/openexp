"""OpenExp — Q-Value Layer for Value-Driven Memory Retrieval.

Q-learning on episodic memory: memories that lead to productive sessions
get higher Q-values and are prioritized in future retrieval.

Q-update formula: Q_new = clamp(Q_old + alpha * reward, q_floor, q_ceiling)
Scoring formula: z_norm(sim) * w_sim + z_norm(q) * w_q

Per-experience Q-values: the same memory can have different Q-values
under different experiences (e.g., "default", "sales", "coding").
Cache format: {memory_id: {experience_name: {q_value, q_action, ...}, ...}}
"""
import fcntl
import json
import logging
import random
import shutil
import statistics
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Q-learning defaults
DEFAULT_Q_CONFIG = {
    "alpha": 0.25,          # learning rate (additive increment per reward)
    "gamma": 0.0,           # discount factor (single-step, no lookahead)
    "epsilon": 0.1,          # exploration probability
    "q_init": 0.0,           # initial Q-value for new memories (earn value from zero)
    "q_floor": -0.5,         # minimum Q-value
    "q_ceiling": 1.0,        # maximum Q-value
    "w_sim": 0.5,            # weight for similarity in combined score
    "w_q": 0.3,              # weight for Q-value in combined score
    "w_recency": 0.1,        # weight for recency
    "w_importance": 0.1,     # weight for importance
    # Three Q-layer weights
    "q_action_weight": 0.5,
    "q_hypothesis_weight": 0.2,
    "q_fit_weight": 0.3,
    # Normalization
    "use_z_score": True,
    "z_clamp": 3.0,
    "sim_norm_mean": 0.2,
    "sim_norm_std": 0.1,
}

# Q-value layer names
Q_LAYERS = ("action", "hypothesis", "fit")


def compute_layer_rewards(reward: float) -> Dict[str, float]:
    """Compute per-layer rewards: action=full, hypothesis=discounted, fit=asymmetric."""
    return {
        "action": reward,
        "hypothesis": reward * 0.8,
        "fit": reward if reward > 0 else reward * 0.5,
    }


def _is_newer(candidate: Dict, existing: Dict) -> bool:
    """Return True if candidate has a more recent q_updated_at than existing."""
    c_ts = candidate.get("q_updated_at", "")
    e_ts = existing.get("q_updated_at", "")
    if not e_ts:
        return True
    if not c_ts:
        return False  # candidate has no timestamp, can't prove it's newer
    return c_ts > e_ts


def _is_flat_format(data: dict) -> bool:
    """Detect whether Q-cache is in old flat format.

    Flat format: {mem_id: {q_value: ..., q_action: ..., ...}}
    Nested format: {mem_id: {experience_name: {q_value: ..., ...}, ...}}

    Heuristic: if the first entry's value has a "q_value" key directly,
    it's flat format. If the first key maps to another dict that contains
    experience names, it's nested.
    """
    if not data:
        return False
    first_value = next(iter(data.values()))
    if not isinstance(first_value, dict):
        return False
    # Flat format has q_value directly in the value dict
    return "q_value" in first_value


def _migrate_flat_to_nested(data: dict) -> dict:
    """Wrap each flat entry under the "default" experience key."""
    return {mem_id: {"default": q_data} for mem_id, q_data in data.items()}


class QCache:
    """Fast in-memory Q-value cache with LRU eviction.

    Stores per-experience Q-values:
    {memory_id: {experience: {q_value, q_action, ...}, ...}}
    """

    def __init__(self, max_size: int = 100_000):
        self._cache: OrderedDict[str, Dict[str, Dict[str, float]]] = OrderedDict()
        self._max_size = max_size
        self._dirty: Dict[str, Dict] = {}
        self._migrated = False

    def get(self, memory_id: str, experience: str = "default") -> Optional[Dict[str, float]]:
        """Get Q-data for a memory under a specific experience."""
        if memory_id in self._cache:
            self._cache.move_to_end(memory_id)
            return self._cache[memory_id].get(experience)
        return None

    def set(self, memory_id: str, q_data: Dict[str, float], experience: str = "default"):
        """Set Q-data for a memory under a specific experience."""
        if memory_id not in self._cache:
            self._cache[memory_id] = {}
        self._cache[memory_id][experience] = q_data
        self._cache.move_to_end(memory_id)

        if memory_id not in self._dirty:
            self._dirty[memory_id] = {}
        self._dirty[memory_id][experience] = q_data

        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def get_all_q_values(self, experience: str = "default") -> List[float]:
        """Get all Q-values for a specific experience."""
        values = []
        for mem_data in self._cache.values():
            exp_data = mem_data.get(experience)
            if exp_data:
                values.append(exp_data.get("q_value", DEFAULT_Q_CONFIG["q_init"]))
        return values

    def get_experiences_for_memory(self, memory_id: str) -> List[str]:
        """List experiences that have Q-data for this memory."""
        if memory_id in self._cache:
            return list(self._cache[memory_id].keys())
        return []

    def get_experience_stats(self, experience: str = "default") -> Dict[str, Any]:
        """Get stats for a specific experience across all memories."""
        q_values = self.get_all_q_values(experience)
        if not q_values:
            return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0}
        return {
            "count": len(q_values),
            "mean": round(sum(q_values) / len(q_values), 4),
            "min": round(min(q_values), 4),
            "max": round(max(q_values), 4),
        }

    def __len__(self):
        return len(self._cache)

    def save(self, path: Path):
        data = {k: v for k, v in self._cache.items()}
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False))
        tmp_path.rename(path)

    def load(self, path: Path):
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load Q-cache from %s: %s", path, e)
                return

            # Auto-migrate flat format to nested
            if _is_flat_format(data):
                logger.info("Detected flat Q-cache format, migrating to nested (per-experience)")
                # Backup original
                backup_path = path.with_suffix(".json.bak")
                if not backup_path.exists():
                    try:
                        shutil.copy2(path, backup_path)
                        logger.info("Backed up original Q-cache to %s", backup_path)
                    except OSError as e:
                        logger.warning("Failed to backup Q-cache: %s", e)
                data = _migrate_flat_to_nested(data)
                self._migrated = True

            for k, v in data.items():
                self._cache[k] = v
                self._cache.move_to_end(k)
                while len(self._cache) > self._max_size:
                    self._cache.popitem(last=False)

    def save_delta(self, deltas_dir: Path, session_id: str):
        """Save only changed entries as a delta file."""
        if not self._dirty:
            return
        deltas_dir.mkdir(parents=True, exist_ok=True)
        delta_path = deltas_dir / f"q_delta_{session_id}.json"
        data = {k: v for k, v in self._dirty.items()}
        delta_path.write_text(json.dumps(data, ensure_ascii=False))
        self._dirty.clear()

    def load_and_merge(self, path: Path, deltas_dir: Path):
        """Load main cache, then merge all pending deltas.

        Uses fcntl.flock to prevent concurrent load_and_merge operations
        from corrupting the cache file.
        """
        lock_path = path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self.load(path)
            if deltas_dir.exists():
                merged_any = False
                for delta_file in sorted(deltas_dir.glob("q_delta_*.json")):
                    try:
                        delta_data = json.loads(delta_file.read_text())

                        # Auto-migrate delta if flat
                        if _is_flat_format(delta_data):
                            delta_data = _migrate_flat_to_nested(delta_data)

                        for mem_id, exp_dict in delta_data.items():
                            if mem_id not in self._cache:
                                self._cache[mem_id] = {}
                            for exp_name, q_data in exp_dict.items():
                                existing = self._cache[mem_id].get(exp_name)
                                if existing is None or _is_newer(q_data, existing):
                                    self._cache[mem_id][exp_name] = q_data
                            self._cache.move_to_end(mem_id)
                            while len(self._cache) > self._max_size:
                                self._cache.popitem(last=False)
                        delta_file.unlink()
                        merged_any = True
                    except (json.JSONDecodeError, OSError) as e:
                        logger.warning("Failed to merge delta %s: %s", delta_file, e)
                if merged_any:
                    self.save(path)
            if self._migrated:
                if not merged_any:
                    self.save(path)
                self._migrated = False
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


class QValueUpdater:
    """Applies Q-learning updates to memory items.

    Supports three Q-layers: action, hypothesis, fit.
    Combined Q = w_action * Q_action + w_hypothesis * Q_hypothesis + w_fit * Q_fit
    """

    def __init__(self, config: Optional[Dict] = None, cache: Optional[QCache] = None):
        self.cfg = {**DEFAULT_Q_CONFIG, **(config or {})}
        self.cache = cache if cache is not None else QCache()

    def update(
        self,
        memory_id: str,
        reward: float,
        layer: str = "action",
        next_max_q: Optional[float] = None,
        experience: str = "default",
    ) -> Dict[str, float]:
        """Apply additive Q-learning update to a specific Q-layer.

        Formula: Q_new = clamp(Q_old + alpha * reward, q_floor, q_ceiling)
        Each positive reward ADDS to Q-value; each negative SUBTRACTS.
        """
        alpha = self.cfg["alpha"]
        gamma = self.cfg["gamma"]
        q_floor = self.cfg["q_floor"]
        q_ceiling = self.cfg.get("q_ceiling", 1.0)

        q_data = self.cache.get(memory_id, experience) or self._default_q_data()
        target = float(reward) + gamma * float(next_max_q or 0.0)

        layer_key = f"q_{layer}"
        old_q = q_data.get(layer_key, self.cfg["q_init"])
        new_q = old_q + alpha * target

        if q_floor is not None:
            new_q = max(q_floor, new_q)
        new_q = min(q_ceiling, new_q)

        q_data[layer_key] = new_q
        q_data["q_value"] = self._combined_q(q_data)
        q_data["q_visits"] = q_data.get("q_visits", 0) + 1
        q_data["last_reward"] = float(reward)
        q_data["last_layer_updated"] = layer
        q_data["q_updated_at"] = datetime.now(timezone.utc).isoformat()

        self.cache.set(memory_id, q_data, experience)
        return q_data

    def update_all_layers(
        self,
        memory_id: str,
        rewards: Dict[str, float],
        experience: str = "default",
    ) -> Dict[str, float]:
        """Update multiple Q-layers at once (additive)."""
        q_data = self.cache.get(memory_id, experience) or self._default_q_data()
        q_ceiling = self.cfg.get("q_ceiling", 1.0)

        for layer, reward in rewards.items():
            if layer in Q_LAYERS:
                layer_key = f"q_{layer}"
                old_q = q_data.get(layer_key, self.cfg["q_init"])
                target = float(reward)
                new_q = old_q + self.cfg["alpha"] * target
                if self.cfg["q_floor"] is not None:
                    new_q = max(self.cfg["q_floor"], new_q)
                new_q = min(q_ceiling, new_q)
                q_data[layer_key] = new_q

        q_data["q_value"] = self._combined_q(q_data)
        q_data["q_visits"] = q_data.get("q_visits", 0) + 1
        q_data["q_updated_at"] = datetime.now(timezone.utc).isoformat()

        self.cache.set(memory_id, q_data, experience)
        return q_data

    def batch_update(
        self,
        memory_ids: List[str],
        reward: float,
        layer: str = "action",
        experience: str = "default",
    ) -> Dict[str, Dict[str, float]]:
        """Update Q-values for a batch of memories with the same reward."""
        results = {}
        for mem_id in memory_ids:
            results[mem_id] = self.update(mem_id, reward, layer, experience=experience)
        return results

    def _combined_q(self, q_data: Dict[str, float]) -> float:
        """Compute weighted combination of three Q-layers."""
        return (
            self.cfg["q_action_weight"] * q_data.get("q_action", self.cfg["q_init"])
            + self.cfg["q_hypothesis_weight"] * q_data.get("q_hypothesis", self.cfg["q_init"])
            + self.cfg["q_fit_weight"] * q_data.get("q_fit", self.cfg["q_init"])
        )

    def _default_q_data(self) -> Dict[str, float]:
        q_init = self.cfg["q_init"]
        return {
            "q_action": q_init,
            "q_hypothesis": q_init,
            "q_fit": q_init,
            "q_value": q_init,
            "q_visits": 0,
            "q_updated_at": datetime.now(timezone.utc).isoformat(),
        }


class QValueScorer:
    """Re-ranks retrieval results using Q-value + similarity hybrid scoring.

    Uses z-score normalization for putting similarity and Q-value
    on the same scale before combining.
    """

    def __init__(self, config: Optional[Dict] = None, cache: Optional[QCache] = None):
        self.cfg = {**DEFAULT_Q_CONFIG, **(config or {})}
        self.cache = cache if cache is not None else QCache()

    def rerank(
        self,
        candidates: List[Dict[str, Any]],
        top_k: int = 5,
        experience: str = "default",
    ) -> List[Dict[str, Any]]:
        """Re-rank candidates using hybrid similarity + Q-value scoring."""
        if not candidates:
            return []

        enriched = []
        for c in candidates:
            c_copy = c.copy()
            mem_id = c.get("id", c.get("memory_id", ""))

            q_data = self.cache.get(str(mem_id), experience)
            if q_data is None:
                meta = c.get("metadata", {})
                q_data = {
                    "q_value": meta.get("q_value", self.cfg["q_init"]),
                    "q_action": meta.get("q_action", self.cfg["q_init"]),
                    "q_hypothesis": meta.get("q_hypothesis", self.cfg["q_init"]),
                    "q_fit": meta.get("q_fit", self.cfg["q_init"]),
                }

            c_copy["q_data"] = q_data
            c_copy["q_estimate"] = q_data.get("q_value", self.cfg["q_init"])
            enriched.append(c_copy)

        for c in enriched:
            c["sim_raw"] = c.get("hybrid_score", c.get("composite_score", c.get("score", 0.5)))

        if self.cfg["use_z_score"]:
            enriched = self._apply_z_score(enriched)
        else:
            for c in enriched:
                c["sim_norm"] = c["sim_raw"]
                c["q_norm"] = c["q_estimate"]

        w_sim = self.cfg["w_sim"]
        w_q = self.cfg["w_q"]
        w_rec = self.cfg["w_recency"]
        w_imp = self.cfg["w_importance"]

        for c in enriched:
            recency = c.get("recency_score", 0.5)
            importance = c.get("importance_score", 0.5)
            c["combined_score"] = (
                w_sim * c["sim_norm"]
                + w_q * c["q_norm"]
                + w_rec * recency
                + w_imp * importance
            )

        enriched.sort(key=lambda x: x["combined_score"], reverse=True)

        # Epsilon-greedy exploration
        epsilon = self.cfg["epsilon"]
        if epsilon > 0 and random.random() < epsilon and len(enriched) > top_k:
            greedy = enriched[:top_k - 1]
            rest = enriched[top_k - 1:]
            random_pick = random.choice(rest)
            selected = greedy + [random_pick]
            selected.sort(key=lambda x: x["combined_score"], reverse=True)
        else:
            selected = enriched[:top_k]

        return selected

    def _apply_z_score(self, candidates: List[Dict]) -> List[Dict]:
        """Z-score normalization for similarity and Q-values."""
        z_clamp = self.cfg["z_clamp"]

        sim_mean = self.cfg["sim_norm_mean"]
        sim_std = self.cfg["sim_norm_std"] if self.cfg["sim_norm_std"] > 1e-9 else 1.0

        q_values = [c["q_estimate"] for c in candidates]
        if len(q_values) > 1:
            q_mean = statistics.fmean(q_values)
            q_std = statistics.pstdev(q_values)
            if q_std < 1e-9:
                q_std = 1.0
        else:
            q_mean = q_values[0] if q_values else 0.5
            q_std = 1.0

        for c in candidates:
            sim_z = (c["sim_raw"] - sim_mean) / sim_std
            c["sim_norm"] = max(-z_clamp, min(z_clamp, sim_z))

            q_z = (c["q_estimate"] - q_mean) / q_std
            c["q_norm"] = max(-z_clamp, min(z_clamp, q_z))

        return candidates

    def calibrate_corpus_stats(self, similarities: List[float]):
        """Calibrate similarity normalization from a corpus of scores."""
        if similarities:
            self.cfg["sim_norm_mean"] = statistics.fmean(similarities)
            self.cfg["sim_norm_std"] = statistics.pstdev(similarities)
            logger.info(
                "Calibrated sim stats: mean=%.4f, std=%.4f",
                self.cfg["sim_norm_mean"],
                self.cfg["sim_norm_std"],
            )

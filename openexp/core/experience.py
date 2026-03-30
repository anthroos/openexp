"""Experience — domain-specific Q-value contexts.

An Experience defines how Q-values are computed and rewarded in a specific
domain (e.g., sales, coding, devops). The same memory can have different
Q-values under different experiences.

Search order for loading:
  1. ~/.openexp/experiences/{name}.yaml
  2. openexp/data/experiences/{name}.yaml (shipped with repo)
  3. DEFAULT_EXPERIENCE constant
"""
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Shipped experiences directory (inside the package)
_BUNDLED_DIR = Path(__file__).parent.parent / "data" / "experiences"


@dataclass
class ProcessStage:
    """A stage in a business process pipeline."""

    name: str
    description: str = ""
    reward_on_enter: float = 0.0


@dataclass
class Experience:
    """A domain-specific Q-value context."""

    name: str
    description: str
    session_reward_weights: Dict[str, float] = field(default_factory=dict)
    outcome_resolvers: List[str] = field(default_factory=list)
    retrieval_boosts: Dict[str, float] = field(default_factory=dict)
    q_config_overrides: Dict[str, float] = field(default_factory=dict)
    process_stages: List[ProcessStage] = field(default_factory=list)
    reward_memory_types: List[str] = field(default_factory=list)


DEFAULT_EXPERIENCE = Experience(
    name="default",
    description="General-purpose experience with balanced weights",
    session_reward_weights={
        "commit": 0.3,
        "pr": 0.2,
        "writes": 0.02,
        "deploy": 0.1,
        "tests": 0.1,
        "decisions": 0.1,
        "base": -0.1,
        "min_obs_penalty": -0.05,
        "no_output_penalty": -0.1,
    },
    outcome_resolvers=[],
    retrieval_boosts={},
    q_config_overrides={},
)


def _user_experiences_dir() -> Path:
    """Return user-level experiences directory (configurable via env)."""
    from .config import EXPERIENCES_DIR
    return EXPERIENCES_DIR


def _parse_process_stages(raw: list) -> List[ProcessStage]:
    """Parse process_stages from YAML — supports dict and string formats."""
    stages = []
    for item in raw:
        if isinstance(item, dict):
            stages.append(ProcessStage(
                name=item.get("name", ""),
                description=item.get("description", ""),
                reward_on_enter=float(item.get("reward_on_enter", 0.0)),
            ))
        elif isinstance(item, str):
            stages.append(ProcessStage(name=item))
        else:
            logger.warning("Skipping invalid process_stage entry: %s", item)
    return stages


def _parse_yaml(path: Path) -> Experience:
    """Parse a YAML file into an Experience."""
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Invalid experience YAML: {path}")

    raw_stages = data.get("process_stages", [])
    process_stages = _parse_process_stages(raw_stages) if raw_stages else []

    return Experience(
        name=data.get("name", path.stem),
        description=data.get("description", ""),
        session_reward_weights=data.get("session_reward_weights", {}),
        outcome_resolvers=data.get("outcome_resolvers", []),
        retrieval_boosts=data.get("retrieval_boosts", {}),
        q_config_overrides=data.get("q_config_overrides", {}),
        process_stages=process_stages,
        reward_memory_types=data.get("reward_memory_types", []),
    )


_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_experience_name(name: str) -> bool:
    """Validate experience name to prevent path traversal."""
    return bool(_VALID_NAME_RE.match(name)) and len(name) <= 64


def load_experience(name: str) -> Experience:
    """Load an experience by name.

    Search order:
      1. ~/.openexp/experiences/{name}.yaml
      2. openexp/data/experiences/{name}.yaml
      3. DEFAULT_EXPERIENCE (if name == "default")
    """
    if not _validate_experience_name(name):
        logger.warning("Invalid experience name '%s', falling back to default", name)
        return DEFAULT_EXPERIENCE

    if name == "default":
        # Try YAML files first, fall back to constant
        for directory in (_user_experiences_dir(), _BUNDLED_DIR):
            path = directory / f"{name}.yaml"
            if path.exists():
                try:
                    return _parse_yaml(path)
                except Exception as e:
                    logger.warning("Failed to parse %s: %s", path, e)
        return DEFAULT_EXPERIENCE

    # Non-default: must find a YAML file
    for directory in (_user_experiences_dir(), _BUNDLED_DIR):
        path = directory / f"{name}.yaml"
        if path.exists():
            return _parse_yaml(path)

    logger.warning("Experience '%s' not found, falling back to default", name)
    return DEFAULT_EXPERIENCE


def resolve_experience_name(cwd: Optional[str] = None) -> str:
    """Resolve the experience name for a given working directory.

    Priority:
      1. {cwd}/.openexp.yaml → read 'experience' field
      2. OPENEXP_EXPERIENCE env var
      3. "default"
    """
    if cwd:
        project_config = Path(cwd) / ".openexp.yaml"
        if project_config.exists():
            try:
                data = yaml.safe_load(project_config.read_text())
                if isinstance(data, dict) and "experience" in data:
                    return data["experience"]
            except Exception as e:
                logger.warning("Failed to read %s: %s", project_config, e)

    from .config import ACTIVE_EXPERIENCE
    return ACTIVE_EXPERIENCE


def get_active_experience(cwd: Optional[str] = None) -> Experience:
    """Get the currently active experience.

    Checks project-level .openexp.yaml first, then OPENEXP_EXPERIENCE env var.
    """
    name = resolve_experience_name(cwd)
    return load_experience(name)


def list_experiences() -> List[Experience]:
    """List all available experiences from both directories."""
    seen = set()
    experiences = []

    for directory in (_user_experiences_dir(), _BUNDLED_DIR):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.yaml")):
            if path.stem in seen:
                continue
            seen.add(path.stem)
            try:
                experiences.append(_parse_yaml(path))
            except Exception as e:
                logger.warning("Failed to parse %s: %s", path, e)

    # Always include default if not found in YAML
    if "default" not in seen:
        experiences.insert(0, DEFAULT_EXPERIENCE)

    return experiences

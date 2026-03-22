"""Filters for trivial observations that shouldn't be stored in Qdrant.

Expected result: ~60-70% of observations get filtered out.
"""
import re
from typing import Dict

_READONLY_PATTERNS = [
    r"^(git\s+(status|log|diff|show|branch|remote|stash\s+list))",
    r"^(find|grep|rg|ls|cat|head|tail|wc|du|tree|stat)\b",
    r"^(docker\s+(ps|inspect|logs))",
    r"^(curl\s+-s|pgrep|ps\s+aux|launchctl\s+list)",
    r"^(echo|printf|which|type|command\s+-v)\b",
    r"^(jq\b.*\|\s*(cat|head))",
]
_READONLY_RE = re.compile("|".join(_READONLY_PATTERNS))

_MEANINGFUL_PATTERNS = [
    r"git\s+(commit|push|merge|rebase|cherry-pick)",
    r"gh\s+(pr|issue|release)",
    r"(deploy|npm\s+publish|pip\s+install|make\s+install)",
    r"(pytest|npm\s+test|make\s+test)",
    r"docker\s+(build|run|compose|push)",
]
_MEANINGFUL_RE = re.compile("|".join(_MEANINGFUL_PATTERNS))

_VALUABLE_TAGS = {"crm_update", "skill_update", "decision", "deployment", "error"}
_MIN_SUMMARY_LEN = 20


def should_keep(obs: Dict) -> bool:
    """Return True if observation is worth ingesting into Qdrant."""
    summary = obs.get("summary", "")
    tool = obs.get("tool", "")
    tags = set(obs.get("tags", []))
    obs_type = obs.get("type", "")

    if tags & _VALUABLE_TAGS:
        return True
    if obs_type in ("decision", "retrospective"):
        return True
    if tool in ("Write", "Edit"):
        return True
    if tool == "transcript_extraction":
        return True
    if len(summary) < _MIN_SUMMARY_LEN:
        return False

    if tool == "Bash":
        cmd = obs.get("context", {}).get("command", summary)
        if cmd.startswith("Ran: "):
            cmd = cmd[5:]
        if _MEANINGFUL_RE.search(cmd):
            return True
        if _READONLY_RE.search(cmd):
            return False
        return True

    return True

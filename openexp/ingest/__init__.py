"""OpenExp Ingest — Transcript + decision pipeline into Qdrant.

Public API:
    ingest_transcript()  — full conversation → Qdrant
    _load_configured_resolvers() — outcome resolver loading
"""
import importlib
import logging
from typing import List

logger = logging.getLogger(__name__)


def _load_configured_resolvers() -> List:
    """Load outcome resolvers from OPENEXP_OUTCOME_RESOLVERS env var.

    Format: "module:ClassName,module2:ClassName2"
    Example: "openexp.resolvers.crm_csv:CRMCSVResolver"
    """
    from ..core.config import OUTCOME_RESOLVERS

    if not OUTCOME_RESOLVERS:
        return []

    ALLOWED_PREFIX = "openexp.resolvers."

    resolvers = []
    for entry in OUTCOME_RESOLVERS.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            module_path, class_name = entry.rsplit(":", 1)
            if not module_path.startswith(ALLOWED_PREFIX):
                logger.error("Rejected resolver %s: must start with %s", module_path, ALLOWED_PREFIX)
                continue
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            resolvers.append(cls())
            logger.info("Loaded outcome resolver: %s", entry)
        except Exception as e:
            logger.error("Failed to load resolver %s: %s", entry, e)

    return resolvers

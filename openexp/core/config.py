"""Centralized configuration for OpenExp.

All paths configurable via environment variables with sensible defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present (project root = 2 levels up from core/config.py)
_project_root = Path(__file__).parent.parent.parent
load_dotenv(_project_root / ".env")

# Data directory for Q-cache, predictions, retrievals
DATA_DIR = Path(os.getenv("OPENEXP_DATA_DIR", os.path.expanduser("~/.openexp/data")))

# Embedding model (FastEmbed BAAI/bge-small-en-v1.5, 384 dims — local, free, no API key)
EMBEDDING_MODEL = os.getenv("OPENEXP_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = int(os.getenv("OPENEXP_EMBEDDING_DIM", "384"))

# Data file paths
Q_CACHE_PATH = DATA_DIR / "q_cache.json"

# Qdrant
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION_NAME = os.getenv("OPENEXP_COLLECTION", "openexp_memories")

# API keys (optional — only needed for enrichment/reflection)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# Ingest — observation pipeline
OBSERVATIONS_DIR = Path(os.getenv(
    "OPENEXP_OBSERVATIONS_DIR",
    os.path.expanduser("~/.claude-memory/observations")
))
SESSIONS_DIR = Path(os.getenv(
    "OPENEXP_SESSIONS_DIR",
    os.path.expanduser("~/.claude-memory/sessions")
))
INGEST_WATERMARK_PATH = DATA_DIR / "ingest_watermark.json"
INGEST_BATCH_SIZE = int(os.getenv("OPENEXP_INGEST_BATCH_SIZE", "50"))

# Enrichment model (optional — requires ANTHROPIC_API_KEY)
ENRICHMENT_MODEL = os.getenv("OPENEXP_ENRICHMENT_MODEL", "claude-haiku-4-5-20251001")

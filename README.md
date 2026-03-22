# OpenExp

**Q-learning memory for Claude Code — your AI learns from experience.**

Every Claude Code session starts from zero. OpenExp changes that. It gives Claude Code persistent memory that **learns** which memories are useful and which aren't, using Q-learning (the same technique behind AlphaGo).

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                    Claude Code Session                   │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  SessionStart Hook ──→ Search Qdrant ──→ Inject Context │
│        ↑                                    │           │
│        │              Q-values rank         │           │
│        │              which memories        ↓           │
│        │              matter most     Claude works...   │
│        │                                    │           │
│  PostToolUse Hook ←── Capture observations ←┘           │
│        │                                                │
│        ↓                                                │
│  Ingest Pipeline ──→ Qdrant (vector DB)                 │
│        │                                                │
│        ↓                                                │
│  Reward Signal ──→ Q-value Update                       │
│  (commits, PRs,     (memories that led to              │
│   tests passed)      good outcomes get                  │
│                      higher scores)                     │
└─────────────────────────────────────────────────────────┘
```

**The loop:**
1. **Remember** — PostToolUse hook captures what Claude does (file edits, commands, decisions)
2. **Recall** — SessionStart hook searches for relevant memories and injects them as context
3. **Learn** — Sessions that produce commits/PRs/tests reward the memories that were recalled
4. **Improve** — Next time, more useful memories float to the top via Q-value ranking

## Quick Start

```bash
# Clone
git clone https://github.com/anthroos/openexp.git
cd openexp

# Install (creates venv, starts Qdrant, registers with Claude Code)
./setup.sh

# Done! Open Claude Code in any project
claude
```

**Prerequisites:** Python 3.11+, Docker, jq

## What You'll See

After a few sessions, when you start Claude Code, you'll see something like:

```
# OpenExp Memory (Q-value ranked)
Query: my-project | Monday 2026-03-22

## Relevant Context
[sim=0.82 q=0.73] Fixed auth bug by adding token refresh logic in api/auth.py
[sim=0.76 q=0.65] Project uses FastAPI + PostgreSQL, deployed on Railway
[sim=0.71 q=0.58] User prefers pytest with fixtures, not unittest
```

The `q=0.73` means this memory has been useful in past sessions (led to commits, PRs). Memories with low Q-values naturally sink.

## MCP Tools

OpenExp exposes these tools to Claude Code via MCP:

| Tool | Description |
|------|-------------|
| `search_memory` | Search with hybrid BM25 + vector + Q-value scoring |
| `add_memory` | Store a new memory with auto-enrichment |
| `log_prediction` | Track a prediction for later outcome resolution |
| `log_outcome` | Resolve prediction with reward, updates Q-values |
| `get_agent_context` | Full context: memories + pending predictions |
| `reflect` | Review recent memories for patterns |
| `memory_stats` | Q-cache size, prediction accuracy |
| `reload_q_cache` | Reload Q-values from disk |

## How Q-Learning Works

Every memory has a Q-value (starts at 0.5). When a memory is recalled at session start and that session is productive (commits, PRs, passed tests), the memory's Q-value increases. Unproductive sessions decrease it.

```
Q_new = (1 - α) × Q_old + α × reward

α = 0.25 (learning rate)
reward ∈ [-0.5, 0.5] (based on session productivity)
```

Three Q-layers capture different aspects:
- **action** (50%): Did this memory help get work done?
- **hypothesis** (20%): Was the information accurate?
- **fit** (30%): Was it relevant to the context?

## Configuration

All settings via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant server host |
| `QDRANT_PORT` | `6333` | Qdrant server port |
| `OPENEXP_COLLECTION` | `openexp_memories` | Qdrant collection name |
| `OPENEXP_DATA_DIR` | `~/.openexp/data` | Q-cache, predictions, retrievals |
| `ANTHROPIC_API_KEY` | *(none)* | Optional: enables LLM enrichment |
| `OPENEXP_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Local embedding model (free) |

## CLI

```bash
# Search memories
openexp search -q "authentication flow" -n 5

# Ingest observations into Qdrant
openexp ingest

# Preview what would be ingested
openexp ingest --dry-run

# Show Q-cache stats
openexp stats
```

## Architecture

```
openexp/
├── core/                 # Q-learning memory engine
│   ├── q_value.py        # Q-learning (the core innovation)
│   ├── direct_search.py  # FastEmbed + Qdrant search
│   ├── hybrid_search.py  # BM25 + vector hybrid scoring
│   ├── scoring.py        # Composite relevance scoring
│   ├── lifecycle.py      # 8-state memory lifecycle
│   ├── enrichment.py     # Auto-metadata extraction
│   └── config.py         # Environment-based configuration
│
├── ingest/               # Observation → Qdrant pipeline
│   ├── observation.py    # JSONL observations → embeddings
│   ├── session_summary.py # Session .md files → memories
│   ├── reward.py         # Session productivity → Q-values
│   ├── retrieval_log.py  # Closed-loop reward tracking
│   └── filters.py        # Filter trivial observations
│
├── hooks/                # Claude Code hooks
│   ├── session-start.sh  # Inject memories at startup
│   ├── user-prompt-recall.sh  # Per-message recall
│   └── post-tool-use.sh  # Capture observations
│
├── mcp_server.py         # MCP server (8 tools)
├── cli.py                # CLI tool
└── reward_tracker.py     # Prediction → outcome tracking
```

## Data Flow

1. **PostToolUse hook** writes observations to `~/.claude-memory/observations/*.jsonl`
2. **`openexp ingest`** reads observations, filters trivial ones, embeds, upserts to Qdrant
3. **SessionStart hook** searches Qdrant, injects top memories ranked by Q-value
4. **Reward system** computes session productivity, updates Q-values for recalled memories

## Contributing

PRs welcome! This project is in early stages. Key areas:

- **More reward signals** — beyond commits/PRs, what indicates a productive session?
- **Smarter compaction** — merging duplicate/outdated memories
- **Multi-project** — sharing learnings across projects
- **Benchmarks** — measuring retrieval quality improvement over time

## Research

OpenExp is based on research into Q-learning for episodic memory retrieval, inspired by [MemRL](https://arxiv.org/abs/2404.09560) and applied to AI coding assistants.

## License

MIT

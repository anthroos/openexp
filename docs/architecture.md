# Architecture

## System Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        Claude Code                            │
│                                                              │
│  ┌──────────┐    ┌───────────────┐    ┌──────────────────┐  │
│  │ Session  │    │ User Prompt   │    │   Post Tool Use  │  │
│  │ Start    │    │ Submit        │    │                  │  │
│  └────┬─────┘    └──────┬────────┘    └────────┬─────────┘  │
│       │                 │                      │             │
└───────┼─────────────────┼──────────────────────┼─────────────┘
        │                 │                      │
        ▼                 ▼                      ▼
┌──────────────┐  ┌──────────────┐      ┌──────────────┐
│ session-     │  │ user-prompt- │      │ post-tool-   │
│ start.sh     │  │ recall.sh    │      │ use.sh       │
│              │  │              │      │              │
│ Search →     │  │ Search →     │      │ → Write      │
│ Inject ctx   │  │ Inject ctx   │      │   observation│
└──────┬───────┘  └──────┬───────┘      └──────┬───────┘
       │                 │                      │
       ▼                 ▼                      ▼
┌──────────────────────────────┐    ┌────────────────────┐
│        OpenExp Core          │    │  Observations Dir  │
│                              │    │  ~/.openexp/       │
│  ┌──────────────────────┐   │    │  observations/     │
│  │   direct_search.py   │   │    └─────────┬──────────┘
│  │   FastEmbed + Qdrant │   │              │
│  └──────────┬───────────┘   │              │
│             │               │    ┌─────────▼──────────┐
│  ┌──────────▼───────────┐   │    │   Ingest Pipeline  │
│  │   hybrid_search.py   │   │    │                    │
│  │   BM25 + Vector      │   │    │  observation.py    │
│  └──────────┬───────────┘   │    │  session_summary.py│
│             │               │    │  reward.py         │
│  ┌──────────▼───────────┐   │    │  filters.py        │
│  │    q_value.py        │   │    └─────────┬──────────┘
│  │    Q-learning cache  │   │              │
│  └──────────────────────┘   │              │
│                              │              │
└──────────────┬───────────────┘              │
               │                              │
               ▼                              ▼
        ┌──────────────────────────────────────────┐
        │              Qdrant (Docker)              │
        │         Vector Database (port 6333)       │
        │                                          │
        │  Collection: openexp_memories             │
        │  Vectors: 384-dim (BAAI/bge-small-en-v1.5)│
        └──────────────────────────────────────────┘
```

## Key Components

### Core Engine (`openexp/core/`)

- **config.py** — All settings from environment variables
- **q_value.py** — Q-learning cache with LRU eviction, delta persistence, z-score normalization
- **direct_search.py** — FastEmbed embedding + Qdrant vector search
- **hybrid_search.py** — Pure Python BM25 implementation + hybrid scoring
- **scoring.py** — Composite relevance: semantic + recency + importance + type boost
- **lifecycle.py** — 8-state memory lifecycle with transition validation
- **enrichment.py** — Optional LLM-based metadata extraction
- **v7_extensions.py** — Lifecycle filtering + hybrid scoring helpers

### Ingest Pipeline (`openexp/ingest/`)

Converts raw observations (JSONL) into embedded vectors in Qdrant:

1. **filters.py** — Drops ~60-70% of trivial observations (read-only commands, short summaries)
2. **observation.py** — Batch embeds observations via FastEmbed, upserts to Qdrant
3. **session_summary.py** — Parses session markdown files, creates higher-importance memories
4. **reward.py** — Computes session productivity score, applies Q-value updates
5. **retrieval_log.py** — Tracks which memories were recalled (for closed-loop reward)
6. **watermark.py** — Idempotency: prevents duplicate ingestion

### MCP Server (`openexp/mcp_server.py`)

STDIO-based MCP server exposing 8 tools. Runs as a long-lived process per Claude Code session. Initializes Q-cache on startup, saves delta on shutdown.

### Hooks (`openexp/hooks/`)

Shell scripts registered with Claude Code:

- **session-start.sh** — Builds contextual query, searches Qdrant, formats results, logs retrieval
- **user-prompt-recall.sh** — Per-message recall (skips trivial inputs), logs retrieval
- **post-tool-use.sh** — Captures Write/Edit/Bash observations, skips Read/Glob/Grep

## Data Persistence

| What | Where | Format |
|------|-------|--------|
| Vector embeddings | Qdrant (Docker volume) | 384-dim vectors + JSON payload |
| Q-value cache | `~/.openexp/data/q_cache.json` | `{memory_id: {q_value, q_action, ...}}` |
| Q-value deltas | `~/.openexp/data/deltas/` | Per-session delta files (merged on start) |
| Predictions | `~/.openexp/data/predictions.jsonl` | Agent predictions for outcome tracking |
| Retrieval log | `~/.openexp/data/session_retrievals.jsonl` | Which memories were recalled when |
| Raw observations | `~/.openexp/observations/` | JSONL files per day |
| Session summaries | `~/.openexp/sessions/` | Markdown files per session |
| Ingest watermark | `~/.openexp/data/ingest_watermark.json` | Processed observation IDs |

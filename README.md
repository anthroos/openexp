<p align="center">
  <h1 align="center">OpenExp</h1>
  <p align="center">
    <strong>Skills tell your AI how. OpenExp teaches it what works.</strong><br>
    Outcome-based learning for AI agents. Q-learning memory that gets smarter with every session.
  </p>
</p>

<p align="center">
  <a href="https://github.com/anthroos/openexp/actions/workflows/tests.yml"><img src="https://github.com/anthroos/openexp/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="https://arxiv.org/abs/2603.07360"><img src="https://img.shields.io/badge/arXiv-2603.07360-b31b1b.svg" alt="arXiv"></a>
  <img src="https://img.shields.io/badge/Made_for-Claude_Code-blueviolet" alt="Made for Claude Code">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#mcp-tools">MCP Tools</a> &middot;
  <a href="#configuration">Configuration</a> &middot;
  <a href="#architecture">Architecture</a> &middot;
  <a href="#contributing">Contributing</a>
</p>

---

You wrote a skill: "how to work with CRM." Your agent follows it perfectly. But it doesn't know that approach A closed deals and approach B didn't. Tomorrow it'll do the same thing as yesterday — even if yesterday didn't work.

**Skills say *how*. OpenExp teaches *what works*.**

Every outcome — commit, closed deal, resolved ticket — feeds back as a reward signal. Memories that led to results get higher Q-values and surface first next time. Noise sinks.

### Example: sales agent

Your agent sent 200 emails this month. Which formulations got replies? Which approaches closed deals? Skills don't know — there's no feedback loop.

```yaml
# .openexp.yaml in your sales project
experience: sales
```

```
1. Define your pipeline:  lead → contacted → qualified → proposal → won
2. Work normally — Claude remembers client preferences, deal context, pricing
3. Deal closes → all memories tagged with that client get rewarded
4. Next similar deal → the insights that led to the close surface first
```

After a month, your agent "knows" not just how to write emails — but which emails lead to results.

## The Problem

Skills and CLAUDE.md solve the "agent doesn't remember" problem. But they're **static instructions** — written once, never learning from outcomes. Your agent follows the playbook perfectly, but doesn't know which plays actually work.

Existing memory tools (Mem0, Zep, LangMem) add storage — but every memory is equally important. A two-month-old note about a deleted feature has the same weight as yesterday's critical architecture decision.

**The missing piece:** there's no learning. No feedback loop from outcomes to retrieval quality.

## The Solution

OpenExp adds a **closed-loop learning system**:

```
Session starts → recall memories (ranked by Q-value)
    ↓
Agent works → observations captured automatically
    ↓
Session ends → productive? (commits, PRs, closed deals, resolved tickets)
    ↓
    YES → reward recalled memories (Q-values go up)
    NO  → penalize them (Q-values go down)
    ↓
Next session → better memories surface first
```

### Outcome-Based Rewards

Beyond session-level heuristics, OpenExp supports **outcome-based rewards** from real business events. When a CRM deal moves from "negotiation" to "won", the memories tagged with that client get rewarded — even if the deal took weeks to close.

```
add_memory(content="Acme prefers Google stack", client_id="comp-acme")
    ↓
... weeks of work ...
    ↓
CRM: Acme deal moves negotiation → won
    ↓
resolve_outcomes → finds memories tagged comp-acme → reward +0.8
```

After a few sessions, OpenExp learns what context actually helps you get work done.

## Why OpenExp?

| Feature | OpenExp | Mem0 | Zep/Graphiti | LangMem |
|---------|---------|------|-------------|---------|
| **Learns from outcomes** | Yes — Q-learning from real business results | No | No | No |
| **Process-aware** | Define pipeline stages with reward signals | No | No | No |
| **Memory type filtering** | Reward only decisions/insights, not noise | No | No | No |
| **Outcome-based rewards** | CRM deal closes → tagged memories get rewarded | No | No | No |
| **Claude Code native** | Zero-config hooks, works out of the box | Requires integration | Requires integration | Requires integration |
| **Local-first** | Qdrant + FastEmbed, no cloud, no API key for core | Cloud API | Cloud or self-hosted | Cloud API |
| **Hybrid retrieval** | BM25 + vector + recency + importance + Q-value (5 signals) | Vector only | Graph + vector | Vector only |
| **Privacy** | All data stays on your machine | Data sent to cloud | Depends on setup | Data sent to cloud |

**The key difference:** skills say how. Memory tools store. OpenExp **learns what works** — from real outcomes.

## Quick Start

```bash
git clone https://github.com/anthroos/openexp.git
cd openexp
./setup.sh
```

That's it. Open Claude Code in any project — it now has memory.

> [!TIP]
> No API key needed for core functionality. Embeddings run locally via FastEmbed. An Anthropic API key is optional — it enables auto-enrichment (type classification, tags, validity windows) but everything works great without it.

**Prerequisites:** Python 3.11+, Docker, jq

## What You'll See

When you open Claude Code after a few sessions:

```
# OpenExp Memory (Q-value ranked)
Query: my-project | Monday 2026-03-22

## Relevant Context
[sim=0.82 q=0.73] Fixed auth bug by adding token refresh logic in api/auth.py
[sim=0.76 q=0.65] Project uses FastAPI + PostgreSQL, deployed on Railway
[sim=0.71 q=0.58] User prefers pytest with fixtures, not unittest
```

`q=0.73` means this memory consistently leads to productive sessions. `q=0.31` means it's been recalled but didn't help — it'll rank lower next time.

## How It Works

Three hooks integrate with Claude Code automatically:

| Hook | When | What |
|------|------|------|
| **SessionStart** | Session opens | Searches Qdrant for relevant memories, injects top results as context |
| **UserPromptSubmit** | Every message | Lightweight recall — adds relevant memories to each prompt |
| **PostToolUse** | After Write/Edit/Bash | Captures what Claude does as observations (JSONL) |
| **SessionEnd** | Session closes | Summary → ingest → reward → decision extraction (async) |

After each session, Opus 4.6 reads the conversation transcript and extracts **decisions** (not actions) — strategic choices, insights, and commitments that have value for future similar situations. See [Decision Extraction](docs/decision-extraction.md).

The MCP server provides 16 tools for memory operations, introspection, and calibration.

### The Learning Loop

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│   ┌─────────┐    search     ┌────────┐    inject    ┌─────┐ │
│   │ Qdrant  │──────────────→│ Scorer │────────────→│ LLM │ │
│   │ (384d)  │               │        │              │     │ │
│   └────┬────┘               └────────┘              └──┬──┘ │
│        │                    BM25 10%                    │    │
│        │                    Vector 30%                  │    │
│   Q-values                  Recency 15%            observations
│   updated                   Importance 15%             │    │
│        │                    Q-value 30%                 │    │
│        │                                               │    │
│   ┌────┴────┐   reward    ┌──────────┐   ingest   ┌───┴──┐ │
│   │ Q-Cache │←────────────│ Reward   │←───────────│ JSONL│ │
│   │  (LRU)  │             │ Tracker  │            │ obs  │ │
│   └─────────┘             └──────────┘            └──────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Q-Learning Details

Every memory has a Q-value (starts at 0.0 — earn value from zero). Three layers capture different aspects:

| Layer | Weight | Measures |
|-------|--------|----------|
| **action** | 50% | Did recalling this help get work done? |
| **hypothesis** | 20% | Was the information accurate? |
| **fit** | 30% | Was it relevant to the context? |

Update rule:

```
Q_new = clamp(Q_old + α × reward, floor, ceiling)

α = 0.25 (learning rate)
reward ∈ [-1.0, 1.0] (productivity signal)
floor = -0.5, ceiling = 1.0
```

Retrieval scoring combines five signals:

```
score = 0.30 × vector_similarity    # semantic match
      + 0.10 × bm25_score           # keyword match
      + 0.15 × recency              # exponential decay (90-day half-life)
      + 0.15 × importance           # type-weighted metadata
      + 0.30 × q_value              # learned quality
```

With 10% epsilon-greedy exploration — occasionally surfaces low-Q memories to give them another chance.

## MCP Tools

**Core — memory operations:**

| Tool | Description |
|------|-------------|
| `search_memory` | Hybrid search: BM25 + vector + Q-value reranking |
| `add_memory` | Store memory with auto-enrichment (type, tags, validity). Supports `client_id` for entity tagging |
| `log_prediction` | Track a prediction for later outcome resolution |
| `log_outcome` | Resolve prediction with reward → updates Q-values |
| `get_agent_context` | Full context: memories + pending predictions |
| `resolve_outcomes` | Run outcome resolvers (CRM stage changes → targeted rewards) |
| `reflect` | Review recent memories for patterns |
| `memory_stats` | Q-cache size, prediction accuracy stats |
| `reload_q_cache` | Hot-reload Q-values from disk |

**Introspection — understand why memories rank the way they do:**

| Tool | Description |
|------|-------------|
| `experience_info` | Active experience config (weights, resolvers, boosts) |
| `experience_top_memories` | Top or bottom N memories by Q-value |
| `experience_insights` | Reward distribution, learning velocity, valuable memory types |
| `calibrate_experience_q` | Manually set Q-value for a memory with reason |
| `memory_reward_history` | Full reward trail: Q-value changes, contexts (L2), cold storage (L3) |
| `reward_detail` | Complete L3 cold storage record for a reward event |
| `explain_q` | Human-readable LLM explanation of why a memory has its Q-value (L4) |

## CLI

```bash
# Search memories
openexp search -q "authentication flow" -n 5

# Ingest observations into Qdrant
openexp ingest

# Preview what would be ingested (dry run)
openexp ingest --dry-run

# Run outcome resolvers (CRM stage changes → rewards)
openexp resolve

# Show Q-cache statistics
openexp stats

# Memory compaction (merge similar memories)
openexp compact --dry-run

# Manage experiences
openexp experience list
openexp experience show sales
openexp experience create        # interactive wizard

# Visualization
openexp viz --replay latest      # session replay
openexp viz --demo               # demo dashboard
```

## Configuration

All settings via environment variables (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant server host |
| `QDRANT_PORT` | `6333` | Qdrant server port |
| `QDRANT_API_KEY` | *(none)* | Optional: Qdrant auth (also passed to Docker) |
| `OPENEXP_COLLECTION` | `openexp_memories` | Qdrant collection name |
| `OPENEXP_DATA_DIR` | `~/.openexp/data` | Q-cache, predictions, retrieval logs |
| `OPENEXP_OBSERVATIONS_DIR` | `~/.openexp/observations` | Where hooks write observations |
| `OPENEXP_SESSIONS_DIR` | `~/.openexp/sessions` | Session summary files |
| `OPENEXP_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (local, free) |
| `OPENEXP_EMBEDDING_DIM` | `384` | Embedding dimensions |
| `OPENEXP_INGEST_BATCH_SIZE` | `50` | Batch size for ingestion |
| `OPENEXP_OUTCOME_RESOLVERS` | *(none)* | Outcome resolvers (format: `module:Class`) |
| `OPENEXP_CRM_DIR` | *(none)* | CRM directory for CRMCSVResolver |
| `ANTHROPIC_API_KEY` | *(none)* | Optional: enables LLM-based enrichment |
| `OPENEXP_ENRICHMENT_MODEL` | `claude-haiku-4-5-20251001` | Model for auto-enrichment |

**Anthropic API key is optional.** Without it, memories get default metadata. With it, each memory is automatically classified (type, importance, tags, validity window).

## Architecture

```
openexp/
├── core/                       # Q-learning memory engine
│   ├── q_value.py              # Q-learning: QCache, QValueUpdater, QValueScorer
│   ├── direct_search.py        # FastEmbed (384d) + Qdrant vector search
│   ├── hybrid_search.py        # BM25 keyword + vector + Q-value hybrid scoring
│   ├── scoring.py              # Composite relevance: similarity × recency × importance
│   ├── lifecycle.py            # 8-state memory lifecycle (active→confirmed→archived→...)
│   ├── experience.py           # Per-domain Q-value contexts (default, sales, dealflow)
│   ├── enrichment.py           # Auto-metadata extraction (LLM or defaults)
│   ├── explanation.py          # L4: LLM-generated reward explanations
│   ├── reward_log.py           # L3: cold storage of reward events
│   ├── compaction.py           # Memory merging/clustering
│   ├── v7_extensions.py        # Lifecycle filter + hybrid scoring integration
│   └── config.py               # Environment-based configuration
│
├── ingest/                     # Observation → Qdrant pipeline
│   ├── observation.py          # JSONL observations → embeddings → Qdrant
│   ├── session_summary.py      # Session .md files → memory objects
│   ├── reward.py               # Session productivity → reward signal
│   ├── retrieval_log.py        # Closed-loop: which memories were recalled
│   ├── watermark.py            # Idempotent ingestion tracking
│   ├── filters.py              # Filter trivial observations
│   └── extract_decisions.py    # Opus 4.6 decision extraction from transcripts
│
├── resolvers/                  # Outcome resolvers (pluggable)
│   └── crm_csv.py              # CRM CSV stage transition → reward events
│
├── data/experiences/           # Shipped experience configs
│   ├── default.yaml            # Software engineering
│   ├── sales.yaml              # Sales & outreach
│   └── dealflow.yaml           # Deal pipeline
│
├── outcome.py                  # Outcome resolution framework
│
├── hooks/                      # Claude Code integration
│   ├── session-start.sh        # Inject Q-ranked memories at startup
│   ├── user-prompt-recall.sh   # Per-message context recall
│   ├── post-tool-use.sh        # Capture observations from tool calls
│   └── session-end.sh          # Summary + ingest + reward (closes the loop)
│
├── mcp_server.py               # MCP STDIO server (16 tools, JSON-RPC 2.0)
├── reward_tracker.py           # Prediction → outcome → Q-value updates
├── viz.py                      # Visualization + session replay
└── cli.py                      # CLI: search, ingest, stats, viz, compact, experience
```

### Memory Lifecycle

Memories move through 8 states to prevent stale context:

```
active ──→ confirmed ──→ outdated ──→ archived ──→ deleted
  │            │                          ↑
  ├──→ contradicted ──────────────────────┘
  ├──→ merged
  └──→ superseded
```

Only `active` and `confirmed` memories are returned in searches. Status weights affect scoring: `confirmed=1.2×`, `active=1.0×`, `outdated=0.5×`, `archived=0.3×`.

### Data Flow

```
PostToolUse hook                                  SessionStart hook
      │                                                 ↑
      ↓                                                 │
~/.openexp/observations/*.jsonl                Qdrant search (top 10)
      │                                          + Q-value reranking
      ↓                                                 ↑
SessionEnd hook ──→ summary .md                         │
      │                                                 │
      ↓ (async)                                         │
openexp ingest ──→ FastEmbed ──→ Qdrant ─────────────────┘
      │                            ↑
      ↓                            │
Q-Cache (q_cache.json) ←── reward signal ←── session productivity
```

## Technical Details

| Component | Choice | Why |
|-----------|--------|-----|
| **Embeddings** | FastEmbed (BAAI/bge-small-en-v1.5) | Local, free, no API key, 384 dimensions |
| **Vector DB** | Qdrant | Fast ANN search, payload filtering, Docker-ready |
| **Q-Cache** | In-memory LRU (100K entries) | Fast lookup, delta-based persistence for concurrent sessions |
| **Transport** | MCP STDIO (JSON-RPC 2.0) | Native Claude Code integration |
| **Hooks** | Bash scripts | Minimal dependencies, shell-level integration |

## Troubleshooting

**Docker / Qdrant won't start:**
```bash
# Check Docker is running
docker info

# Check Qdrant container
docker ps -a | grep openexp-qdrant
docker logs openexp-qdrant
```

**Hooks not firing:**
```bash
# Verify hooks are registered
cat ~/.claude/settings.local.json | jq '.hooks'

# Re-run setup to fix registration
./setup.sh
```

**No memories appearing:**
Memories need to be ingested first. After a few Claude Code sessions:
```bash
openexp ingest --dry-run   # preview what will be ingested
openexp ingest             # ingest into Qdrant
openexp stats              # check Q-cache state
```

## Experiences — Define Your Process

Not everyone writes code. An **Experience** defines what "productive" means for your workflow, including pipeline stages and which memory types matter.

| Experience | Process | Top Signals |
|------------|---------|-------------|
| `default` | backlog → in_progress → review → merged → deployed | commits, PRs, tests |
| `sales` | lead → contacted → qualified → proposal → negotiation → won | decisions, emails, follow-ups |
| `dealflow` | lead → discovery → nda → proposal → negotiation → invoice → paid | proposals, invoices, payments |

Switch with one env var:
```bash
export OPENEXP_EXPERIENCE=dealflow
```

Each experience also controls **which memory types get rewarded** — sales rewards decisions and insights, not raw tool actions. This means the system learns faster because it focuses on the signal, not the noise.

**Create your own** with the interactive wizard:
```bash
openexp experience create
# Pick a process type (dev/sales/support/content)
# Customize stages, signal weights, memory type filters
```

See the [Experiences Guide](docs/experiences.md) for full details.

## Documentation

Detailed docs are available in the [`docs/`](docs/) directory:

- [How It Works](docs/how-it-works.md) — the 4-phase learning cycle
- [Decision Extraction](docs/decision-extraction.md) — Opus 4.6 extracts decisions, not actions
- [Storage System](docs/storage-system.md) — 5-level pyramid (L0-L4), all 4 reward paths
- [Experiences](docs/experiences.md) — domain-specific reward profiles (create your own)
- [Architecture](docs/architecture.md) — system design and data flow
- [Configuration](docs/configuration.md) — all environment variables and options

## Contributing

This project is in early stages. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and workflow.

Key areas where help is welcome:

- **New experiences** — domain-specific reward profiles (DevOps, writing, research, etc.)
- **Outcome resolvers** — new integrations beyond CRM (Jira, Linear, GitHub Issues)
- **Multi-project learning** — sharing relevant context across projects
- **Benchmarks** — measuring retrieval quality improvement over time
- **Automated lifecycle transitions** — contradiction detection, staleness heuristics

## Research

OpenExp implements value-driven memory retrieval inspired by [MemRL](https://arxiv.org/abs/2404.09560), adapted for episodic memory in AI coding assistants.

Core insight: treating memory retrieval as a reinforcement learning problem — where the reward signal comes from real session outcomes — produces better context selection than similarity-only search.

## Citation

If you use OpenExp in your research, please cite:

```bibtex
@article{pasichnyk2026yerkes,
  title={The Yerkes-Dodson Curve for AI Agents: Optimal Pressure in Multi-Agent Survival Games},
  author={Pasichnyk, Ivan},
  journal={arXiv preprint arXiv:2603.07360},
  year={2026},
  url={https://arxiv.org/abs/2603.07360}
}
```

## License

[MIT](LICENSE) &copy; Ivan Pasichnyk

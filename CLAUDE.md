# OpenExp — Development Instructions

## Memory Protocol (MANDATORY)

OpenExp gives Claude Code persistent memory with Q-learning. For it to work, follow this protocol **every task**:

### Before starting any task:
```
search_memory("relevant context for this task")
```
Find prior experience, decisions, mistakes. Hooks do auto-recall on each message, but you MUST do a targeted search before complex tasks.

### After completing a task:
```
add_memory("what was decided/done and why", type="decision")
```
Capture outcomes, not just actions. Q-learning needs explicit signals.

### When the user shares context:
```
add_memory("the context", type="fact")
```
Immediately. Don't wait. Every piece of context improves future retrieval.

## Architecture

**Full reference:** `docs/storage-system.md` — 5-level pyramid (L0–L4), all 4 reward paths, Q-learning formulas, 16 MCP tools, every file and env var. **Read that instead of re-reading source code.**

- `openexp/core/` — Q-learning engine (q_value, search, scoring, lifecycle, explanation, reward_log)
- `openexp/ingest/` — Observation → Qdrant pipeline + session reward (Path 1)
- `openexp/reward_tracker.py` — Prediction → outcome rewards (Path 2)
- `openexp/outcome.py` — Business event rewards (Path 3)
- `openexp/resolvers/` — Outcome resolvers (CRM CSV → rewards)
- `openexp/mcp_server.py` — MCP STDIO server (16 tools) + calibration rewards (Path 4)
- `openexp/cli.py` — CLI interface
- `openexp/viz.py` — Visualization data export
- `tests/` — 237 tests across 11 files

## Q-Learning (do not change without discussion)

- Formula: `Q = clamp(Q + α*reward, floor, ceiling)`
- q_init=0.0, alpha=0.25, floor=-0.5, ceiling=1.0
- Three layers: action (50%), hypothesis (20%), fit (30%)
- Scoring: vector 30%, BM25 10%, recency 15%, importance 15%, Q-value 30%

## Development Workflow

Two remotes: `origin` (private), `public` (open-source).

```bash
# Branch from main
git checkout -b feat/my-feature

# Test
.venv/bin/python3 -m pytest tests/ -v

# Verify no private data
grep -rn "sk-ant\|welababeldata\|ivanpasichnyk" $(git ls-files)

# Push to private first, public when ready
git push origin feat/my-feature    # daily work
git push public main               # releases
```

## Rules

- No hardcoded paths. Everything via env vars.
- No personal data in code (API keys, usernames, company names).
- `.env` is gitignored — never commit it.
- Always branch → PR → squash merge. Never push to main directly.

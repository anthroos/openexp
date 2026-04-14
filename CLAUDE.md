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

### Prediction loop (build judgment over time):
When you make a prediction or recommendation (deal outcome, approach success, client reaction):
```
log_prediction("prediction text", confidence=0.7, memory_ids=["ids-that-informed-this"])
```
Later, when the outcome is known:
```
log_outcome(prediction_id="pred_xxx", outcome="what happened", reward=0.8)
```
This is how Q-learning builds real judgment — not from heuristics, but from verified outcomes.
Use for: deal predictions, strategy recommendations, client behavior forecasts, technical approach bets.

## Architecture

**Full reference:** `docs/storage-system.md` for Q-learning details, `docs/experience-library.md` for the Experience Library pipeline.

- `openexp/core/` — Q-learning engine, hybrid search, scoring, lifecycle
- `openexp/ingest/` — Transcript ingest + Experience Library pipeline (chunking, topic mapping, experience extraction)
- `openexp/mcp_server.py` — MCP STDIO server (5 tools: search_memory, add_memory, log_prediction, log_outcome, memory_stats)
- `openexp/cli.py` — CLI (search, ingest, chunk, topics, stats, compact, experience, viz)
- `scripts/batch_label.py` — Batch experience labeling across all threads
- `tests/` — 300 tests across 13 files

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

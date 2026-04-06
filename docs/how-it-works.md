# How OpenExp Works

## The Problem

Claude Code is stateless. Every session starts from zero. It doesn't remember:
- What you worked on yesterday
- Which approaches worked and which failed
- Your project's architecture and conventions
- Decisions you made and why

## The Solution: Q-Learning Memory

OpenExp adds persistent, learning memory to Claude Code through three mechanisms:

### 1. Observation Capture (PostToolUse Hook)

Every time Claude Code uses a tool (writes a file, runs a command, edits code), the PostToolUse hook captures an observation:

```json
{
  "id": "obs-20260322-a1b2c3d4",
  "tool": "Edit",
  "summary": "Edited file: auth.py",
  "project": "my-app",
  "timestamp": "2026-03-22T10:30:00Z"
}
```

These observations are written to `~/.openexp/observations/` as JSONL files.

### 2. Memory Retrieval (SessionStart Hook)

When you start a new Claude Code session, the SessionStart hook:

1. Builds a contextual query from your project name + recent work
2. Searches Qdrant for relevant memories
3. Ranks results using hybrid scoring:
   - **30%** Semantic similarity (vector search)
   - **10%** Keyword match (BM25)
   - **15%** Recency (exponential decay, 90-day half-life)
   - **15%** Importance (auto-categorized by type)
   - **30%** Q-value (learned usefulness)
4. Injects top results as `additionalContext` before Claude sees your prompt

### 3. Session Summary (SessionEnd Hook)

When the session ends, the SessionEnd hook:

1. Generates a markdown summary from the session's observations
2. Saves it to `~/.openexp/sessions/`
3. Triggers async ingest + reward computation (runs in background so it doesn't block exit)

### 4. Decision Extraction (SessionEnd Phase 2c)

After ingest and reward, Opus 4.6 reads the full conversation transcript and extracts:

- **Decisions** — "Chose to remove advertising from scope because we're not a marketing agency"
- **Insights** — "All won clients came through referrals — zero presence on freelance platforms"
- **Commitments** — "Finalize proposal and send by tomorrow"

This is the critical difference between recording "Edited proposal.html" (action) and recording "Chose to lead with social proof because enterprise clients trust references" (decision with reasoning). Decisions have strategic value; actions don't.

See [Decision Extraction](decision-extraction.md) for full details.

### 5. Q-Learning Reward Loop

This is the core innovation. After each session:

1. **Compute reward**: Did the session produce commits? PRs? Tests? → positive reward. Nothing useful? → negative reward.
2. **Update Q-values**: Memories that were recalled at session start get their Q-values updated based on the session's outcome.
3. **Better retrieval**: Next session, memories with higher Q-values float to the top.

The Q-update formula:
```
Q_new = (1 - 0.25) × Q_old + 0.25 × reward
```

Over time, this creates a natural ranking where useful memories (project conventions, working solutions, important decisions) rise to the top, while noise (trivial commands, one-off fixes) sinks.

## The 4-Phase Learning Cycle

OpenExp learns in four phases, each building on the previous:

**Phase 1 — Store.** Agent works, system writes every action, decision, and context to the vector database. Hooks handle this automatically. Retrieval at this stage = basic vector search.

**Phase 2 — Auto-reward.** After each session, the system evaluates productivity (commits, PRs, deploys, emails sent). Memories from productive sessions get higher Q-values. Noise starts sinking.

**Phase 3 — Decision extraction.** Opus 4.6 reads the conversation transcript and extracts strategic decisions, insights, and commitments. These become first-class memories — the kind of context that changes how you approach the next similar situation.

**Phase 4 — Human calibration.** After a significant outcome (deal closed, project shipped), the user reviews related memories and calibrates Q-values. "This memory directly contributed to closing the deal" → Q goes up. "This was irrelevant noise" → Q goes down.

### What you see over time

| Time | What happens |
|------|-------------|
| **Week 1** | System stores everything. Retrieval = vector search. |
| **Month 1** | Auto-rewards separate productive from empty sessions. Decision extraction adds strategic memories. |
| **Month 3** | Retrieval is fundamentally different from plain search. Proven decisions surface first. Noise is gone. |

## Reward Signals

Reward weights are defined by the active **Experience**. The `default` experience rewards coding; `sales` rewards emails and follow-ups; `dealflow` rewards proposals, invoices, and payments. See [Experiences](experiences.md) for full details and how to create your own.

### Session-Level (Default Experience)

| Signal | Reward | Why |
|--------|--------|-----|
| `git commit` | +0.3 | Code was shipped |
| `gh pr create` | +0.2 | Work was packaged for review |
| File writes | +0.02 each (max +0.2) | Building something |
| Tests passed | +0.1 | Quality verified |
| Deploy | +0.1 | Shipped to production |
| Decision made | +0.1 | Strategic progress |
| Email sent | +0.0 (default) / +0.15 (sales/dealflow) | Outreach activity |
| Proposal sent | +0.0 (default) / +0.25 (dealflow) | Deal advancement |
| Invoice sent | +0.0 (default) / +0.20 (dealflow) | Revenue generation |
| Payment received | +0.0 (default) / +0.30 (dealflow) | Terminal business reward |
| No writes + no commits | -0.1 | Unproductive session |
| Abandoned (< 3 obs) | -0.05 | Session didn't accomplish anything |
| Base | -0.1 | Must earn positive |

### Outcome-Based (Primary)

Outcome resolvers detect real business events and reward the specific memories that contributed:

| CRM Transition | Event | Reward |
|----------------|-------|--------|
| invoiced → paid | `payment_received` | +1.0 |
| negotiation → won | `deal_closed` | +0.8 |
| qualified → proposal | `client_yes` | +0.6 |
| new → qualified | `meaningful_response` | +0.4 |
| * → lost | `deal_lost` | -0.5 |

**How it works:**

```
1. Tag memories with client_id:
   add_memory("Acme prefers Google", client_id="comp-acme")

2. CRM changes detected (deals.csv diff):
   Acme: negotiation → won

3. resolve_outcomes() finds all memories with client_id="comp-acme"
   → applies reward +0.8 to their Q-values

4. Also resolves pending predictions for comp-acme
```

This creates targeted, long-horizon rewards that span weeks or months — not just single sessions.

## Three Q-Layers

Each memory has three Q-value layers, capturing different aspects:

- **action** (50% weight): Did recalling this memory help get work done?
- **hypothesis** (20% weight): Was the information in this memory accurate?
- **fit** (30% weight): Was this memory relevant to the context it was recalled in?

Combined Q = 0.5 × Q_action + 0.2 × Q_hypothesis + 0.3 × Q_fit

## Memory Lifecycle

Memories go through 8 states:

```
active → confirmed → outdated → archived
  ↓         ↓           ↓          ↓
  └── contradicted ──── merged ── deleted
       superseded
```

- **active**: Default state for new memories
- **confirmed**: Accessed multiple times, still valid
- **outdated**: Older than 30 days, may need validation
- **deleted**: Filtered out of search results

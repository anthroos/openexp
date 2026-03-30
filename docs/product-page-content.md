# OpenExp — Product Page Content

> Source of truth for website/landing page. Written for humans, not developers.
> Last updated: 2026-03-26

---

## Headline

**Your AI doesn't learn from outcomes. OpenExp fixes that.**

## Subheadline

A self-labeling experience engine for AI agents. Define your business process — software dev, sales, support — and outcomes automatically label which memories matter. Over time, your AI knows what works.

---

## The Problem

There are three ways people give context to AI agents today.

### 1. Static instructions (CLAUDE.md)

You write a file with rules and preferences. The AI reads it at the start of each session. It works — but it doesn't learn. To change priorities, you edit the file by hand. The AI itself never updates its understanding of what matters.

### 2. Bring everything (full context)

Pack your CRM, project management, chat history, docs — everything — into the context window. The AI has access to it all. But it's expensive (tokens cost money), slow (large contexts = slower responses), and still doesn't scale. At some point, you can't fit it all in.

### 3. Memory services (Mem0, Zep, LangMem)

Store memories in a database. Search and retrieve when relevant. Better than static files — but every memory is equally important. A critical architecture decision and a random grep command have the same weight. There's no learning.

---

## The OpenExp Approach

Write everything. Remember selectively. **Learn from outcomes.**

### How it works

**1. Automatic capture**

Every action in your Claude Code session — file edits, commits, commands, decisions — is automatically recorded as a memory. You don't do anything. Hooks handle it.

**2. Smart retrieval**

Before each response, the system finds 5-10 most relevant memories and injects them into context. Not by similarity alone — by **proven usefulness**.

**3. Reward loop**

After every session, the system looks at what happened:

| Session outcome | Signal |
|----------------|--------|
| Code committed | +0.3 |
| Pull request created | +0.2 |
| Deployed to production | +0.1 |
| Tests passed | +0.1 |
| Nothing produced | -0.1 |

Memories that were used in productive sessions get a higher score. Memories from empty sessions get a lower score.

This is Q-learning — the same algorithm that trained AlphaGo. Applied to your working memory.

**After a month of use, search results are fundamentally different from plain semantic search.** Proven memories surface first. Noise sinks.

---

## Experiences — Your Process, Your Rewards

One memory can be valuable in one context and worthless in another. An Experience defines what "success" means for a specific workflow — including the process pipeline and which memory types matter.

### For a developer (default)

```yaml
process_stages: [backlog, in_progress, review, merged, deployed]
weights:
  commit: 0.3, pr: 0.2, deploy: 0.1, tests: 0.1
reward_memory_types: [decision, insight, outcome, action]
```

### For sales

```yaml
process_stages: [lead, contacted, qualified, proposal, negotiation, won]
weights:
  email_sent: 0.15, proposal_sent: 0.20, payment_received: 0.30
reward_memory_types: [decision, insight, outcome]  # skip raw actions
```

### For support

```yaml
process_stages: [new_ticket, investigating, responded, resolved, closed]
weights:
  ticket_closed: 0.25, email_sent: 0.10
reward_memory_types: [decision, insight, outcome]
```

### For content creation

```yaml
process_stages: [idea, draft, review, published, distributed]
weights:
  writes: 0.05, deploy: 0.20, decisions: 0.15
reward_memory_types: [decision, insight, outcome]
```

**Each memory holds separate scores per experience.** In a sales context, sales-relevant memories surface. In a coding context — coding memories. Memory type filtering ensures only meaningful memories (decisions, insights) accumulate rewards — raw tool observations stay at baseline.

### Example

Memory: *"Discussed NDA with client — lawyers took 2 weeks, 10+7 year term"*

| Experience | Score | Why |
|-----------|-------|-----|
| **coding** | 0.05 | Session had no commits. Useless for coding. |
| **dealflow** | 0.72 | NDA led to proposal, then payment. Very useful for sales. |

Same memory. Different scores. The active lens determines what surfaces.

You can create custom experiences with `openexp experience create` or drop an `.openexp.yaml` into any project folder for automatic per-project switching.

---

## Four Reward Channels

Not just session outcomes. Four ways to feed signals back.

### 1. Session (automatic)

After every session, the system analyzes what was produced and rewards memories accordingly. No manual action required.

### 2. Predictions

Your AI says "I predict the client will sign." Later, you report the actual outcome. The accuracy difference becomes a reward signal.

### 3. Business events

Connect your CRM. When a deal closes or payment arrives, all memories tagged with that client automatically receive a reward. Real business outcomes flow back to the knowledge that contributed.

### 4. Manual calibration

You know best. Mark any memory as valuable or worthless directly. Override the algorithm when you have knowledge it doesn't.

---

## Five Levels of Understanding

A number alone doesn't explain itself. When you see Q=0.8, you don't know why. Each level adds depth.

| Level | What | Purpose |
|-------|------|---------|
| **L0** | Raw session logs | Full audit trail |
| **L1** | Q-value (one number) | Search ranking |
| **L2** | Short notes: "Session +0.30: 2 commits, 1 PR" | Quick context for score changes |
| **L3** | Full record with all context | Detailed audit |
| **L4** | LLM explanation: "This memory helped because it contained the architecture decision for module X" | Human-readable reasoning |

L1-L2 are in memory — fast, used for ranking. L3-L4 are on disk — for when you want to understand why a memory has its score.

Ask any time: `explain_q("memory-id")` — get the full story.

---

## Search: Five Factors

Not just "find similar text." Five components weighted together.

| Factor | Weight | What it does |
|--------|--------|-------------|
| Semantic similarity | 30% | Vector search — meaning, not keywords |
| Q-value | 30% | Proven useful memories rank higher |
| Keywords (BM25) | 10% | Exact matches when they matter |
| Recency | 15% | Recent memories get a small boost |
| Importance | 15% | Decisions outrank commands |

The key: **Q-value is 30% of the ranking.** This means the system's search improves with every session. After 100 sessions, your retrieval is personalized by actual outcomes.

---

## Fully Local

No SaaS. No data leaves your machine.

| Component | Where it runs |
|-----------|--------------|
| **Qdrant** | Docker container on your machine |
| **FastEmbed** | Local embeddings, no API calls |
| **Q-cache** | JSON file on disk |
| **LLM explanations (L4)** | Anthropic API (optional, can be disabled) |

All data lives under `~/.openexp/`. You own everything.

---

## Built for Claude Code

OpenExp integrates through native Claude Code hooks:

| Hook | When | What happens |
|------|------|-------------|
| **Session start** | You open a session | Top memories injected into context |
| **Each message** | You type something | Relevant memories retrieved |
| **After each action** | AI writes/edits/runs | Observation recorded |
| **Session end** | You close | Reward computed, Q-values updated |

Zero manual work. Install, use Claude Code as usual, watch it get smarter.

---

## Quick Start

```bash
# Install
pip install openexp-memory

# Start Qdrant
docker run -d --name openexp-qdrant -p 6333:6333 qdrant/qdrant

# Register hooks with Claude Code
openexp hooks install

# Done. Use Claude Code as normal.
```

---

## Open Source

MIT License. GitHub: [anthroos/openexp](https://github.com/anthroos/openexp)

Based on research: [The Yerkes-Dodson Curve for AI Agents](https://arxiv.org/abs/2603.07360)

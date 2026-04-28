# OpenExp Roadmap

**Last reviewed:** 2026-04-27

This is the honest gap between **vision** (what we describe to potential users) and **shipped** (what works today). The vision is the reason the project exists; the shipped column is what you can actually use right now.

Legend:

- ✅ **shipped** — works today, end-to-end, used in production
- 🟡 **partial** — mechanism exists but requires manual steps, missing connectors, or no UI
- ❌ **vision** — described publicly but not built; on the roadmap

---

## 1 · Capture raw experience

> "You record everything you do."

| Capability | Status | Today |
|---|---|---|
| Claude Code observations (Edit / Write / Bash) | ✅ | `PostToolUse` hook writes JSONL to `~/.openexp/observations/` automatically |
| Memory writes via MCP (`add_memory`) with `client_id` tagging | ✅ | Works when the assistant or user explicitly calls it |
| Connectors for Gmail / Telegram / Calendar / WhatsApp | ❌ | You attach raw exports manually when building a pack |
| Auto-cluster "this set of memories belongs to deal X" | ❌ | Today: manual `client_id` tag. Forget the tag → nothing clusters |

## 2 · Anonymize before publishing

> "Then run it through the anonymizer."

| Capability | Status | Today |
|---|---|---|
| Local anonymizer (`prompts/anonymize.md`) — runs in your Claude Code | ✅ | No data leaves the machine during anonymization |
| Reverse-identification rule in the prompt (industry / role / geography too narrow → generalize) | 🟡 | Added 2026-04-27. It's a prompt instruction, not an automated linter — Claude can still under-apply it |
| Automated leak linter (`validate_pack.py`) — refuse publish if discrimination set < threshold | ❌ | Today: human eye is the check |

## 3 · Submit / review

> "And send it to us for review."

| Capability | Status | Today |
|---|---|---|
| Submission flow | 🟡 | Form on `openexp.ai/create` opens a GitHub Issue. Reviewer (today: Ivan) merges by hand |
| First-party review portal | ❌ | No web UI; review is a GitHub PR conversation |
| Auto-extract metadata server-side | ❌ | Extraction (`prompts/extract_experience.md`) runs **on the author's machine**, not on a server. Author already produces `meta.yaml` before submitting |

## 4 · Marketplace browse + install

> "Anyone can come to the marketplace and look for an experience relevant to them."

| Capability | Status | Today |
|---|---|---|
| Public catalog | 🟡 | The catalog **is** the GitHub repo (`anthroos/openexp/experiences/`). Site `openexp.ai/use` lists currently published packs |
| Browsable filter UI ("show me sales packs that closed in <30 days") | ❌ | Today: read the file list, open `meta.yaml` to inspect tokens |
| Install one pack | ✅ | `cp -r openexp/experiences/<uuid> ~/.claude/skills/openexp:<author>:<slug>/` — auto-discovered by Claude Code |
| Install the `openexp-use` skill that knows how to apply any pack | ✅ | `git clone anthroos/claude-skills` + symlink. One-time, applies to all packs |
| Search "is there a pack relevant to my situation?" | 🟡 | The `openexp-use` skill does this — discovers all installed packs and matches against the user's situation. Across-machines / public-marketplace search = ❌ |

## 5 · Buy a pack

> "Buy a flow or set of flows."

| Capability | Status | Today |
|---|---|---|
| Free tier (MIT-licensed packs) | ✅ | Live now, 1 pack |
| Paid tier (per-pack purchase, subscription, or both) | ❌ | Site form labels paid tier as "in design" |
| Stripe Connect / checkout integration | ❌ | Not built |
| Subscriber gating on pack download | ❌ | Not built — needs paid tier first |

## 6 · Author payout

> "We take a commission, you get your money."

| Capability | Status | Today |
|---|---|---|
| Stripe Connect account onboarding for authors | ❌ | |
| Author dashboard (downloads, revenue, payout history) | ❌ | |
| Commission split logic (platform vs author %) | ❌ | |
| Tax / 1099 / W-8BEN handling | ❌ | |

## 7 · Pack contents — raw vs summarized

> "You can take the raw version, or the compressed-with-conclusions one."

| Capability | Status | Today |
|---|---|---|
| Raw timeline (`trajectory.anonymized.yaml`) | ✅ | The canonical artifact in every pack |
| Facts-only metadata (`meta.yaml`) | ✅ | Schema v3 (2026-04-27) |
| **Summarized-with-conclusions (`experience.yaml`)** | ❌ | **Removed** in schema v3 because it baked the publisher's interpretation into the artifact (one Claude's read, frozen). The reader's Claude derives match on the fly from the raw trajectory |
| Optional opt-in summary layer for authors who want one | ❌ | Could be reintroduced as `summary.yaml` (opt-in, not default). Open question |

> **This is currently a contradiction with the public narrative** which still suggests "raw OR summarized." After the v2 → v3 transition we ship raw only. Either we add an opt-in summary layer back, or we update narratives to "raw only, here's why."

## 8 · AI layer over the marketplace corpus

> "Later we can run AI on it — it'll find points that humans don't see."

| Capability | Status | Today |
|---|---|---|
| Cross-pack meta-analysis (find patterns across packs from different authors) | ❌ | Frontier work; not started |
| Fine-tuning a model on the published-pack corpus | ❌ | Not started — we don't have enough packs yet |
| Predictions ranked across packs (multiple packs vote on a user's situation) | ❌ | The `openexp-use` skill picks one matching pack today; voting / ensembling = vision |

## 9 · Consent + AI training opt-in

> "When you publish, you indicate whether you give us the right to use your data to train AI."

| Capability | Status | Today |
|---|---|---|
| Per-pack license field | ✅ | `meta.yaml` carries `license: MIT` |
| Granular consent (`allow_ai_training: yes/no/specific-models`) | ❌ | Not in the schema yet |
| Public ToS / privacy policy | ❌ | |
| Author identity verification | ❌ | Today: author handle is whatever you wrote in `meta.yaml`. No proof of authorship |

## 10 · Revenue split for AI training contributors

> "We'll split revenue among everyone whose data was selected to train the AI."

| Capability | Status | Today |
|---|---|---|
| Model-output → contributing-pack attribution | ❌ | This is open frontier research (model attribution / influence functions). Not solved publicly by anyone |
| Revenue attribution + payout pipeline | ❌ | Depends on (5), (6), and the attribution mechanism above |

---

## What's shipped end-to-end today (the demo path)

If you want to see the project work right now, this is the path:

1. **Install:**
   ```
   git clone https://github.com/anthroos/claude-skills.git
   ln -s ~/claude-skills/skills/core/openexp-use ~/.claude/skills/openexp-use
   git clone https://github.com/anthroos/openexp.git
   cp -r openexp/experiences/d49e0997 \
     ~/.claude/skills/openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot/
   ```
2. **Restart Claude Code** — both the pack and the `openexp-use` skill auto-discover.
3. **Describe a situation** in your own words. The `openexp-use` skill discovers installed packs, reads each `meta.yaml` + `trajectory.anonymized.yaml`, judges fit, and replies with a cited `relative_day` from the matching trajectory.
4. **If the cited day informs an action,** the skill calls `log_prediction` automatically. When you return with the result, it calls `log_outcome`. Aggregate analysis later.

That's the entire shipped surface. Everything else on this page is roadmap.

## What we're working on next

Ordered by leverage / cost:

1. **Leak linter (`validate_pack.py`)** — automated reverse-identification check before publish. Cheap, prevents the next privacy incident.
2. **Auto-trigger pack draft on CRM `closed_won` event** — already have the resolver; need to wire the trigger to `prompts/extract_experience.md` so a `draft/` pack appears for human review without you running anything.
3. **Optional `summary.yaml` (opt-in)** — let authors who want a written conclusion ship one alongside the raw trajectory. Default stays raw-only.
4. **Browsable catalog filter on `openexp.ai/use`** — token-based filter ("show packs whose `category_tokens` include `<regulated_industry>` and outcome `closed_won`"). No backend needed; filter the static GitHub list client-side.
5. **Connectors for Gmail / Telegram / Calendar** — biggest source-data gap. Probably one connector at a time.
6. **Paid tier + Stripe Connect** — only after we have ~10+ packs from ≥3 authors. Below that, no marketplace economics.
7. **AI training consent flow** — once we have a paid tier, we have a reason to ask the question; until then it's premature.

## How to read this

- **For users:** install path above is the only fully shipped surface. Everything else in this doc is what you can expect, in roughly the order above.
- **For contributors:** anything marked ❌ is fair game. Pick one, file an issue first to align on shape, then PR.
- **For Ivan (founder):** this doc replaces the public-narrative drift. When the post says "marketplace already exists", point at this page. When the post says "AI revenue split", flag it as ❌ before publishing.

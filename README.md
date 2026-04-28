<p align="center">
  <h1 align="center">OpenExp</h1>
  <p align="center">
    <strong>How did this happen? — a hippocampus for AI agents.</strong><br>
    Capture trajectories raw. Grade only when reality returns its verdict. Build a labeled corpus of human-AI decisions tied to grounded outcomes.
  </p>
</p>

<p align="center">
  <a href="https://github.com/anthroos/openexp/actions/workflows/tests.yml"><img src="https://github.com/anthroos/openexp/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <img src="https://img.shields.io/badge/Status-Pilot-orange" alt="Pilot stage">
  <img src="https://img.shields.io/badge/Seeds-1-blue" alt="1 published seed">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#the-pipeline">Pipeline</a> &middot;
  <a href="#publishing-an-experience">Publish</a> &middot;
  <a href="#mcp-tools">MCP Tools</a> &middot;
  <a href="#status">Status</a>
</p>

---

## The Question

When you close a deal, ship a feature, or lose a client — *how did it happen*? Which decisions, in what order, against which context, on which hypotheses? Today's AI agents can't answer that. They follow skills and instructions perfectly, but they don't accumulate grounded knowledge about how outcomes actually arrived.

OpenExp captures every human-AI decision as a step in a trajectory, links those steps into coherent journeys, and grades each journey retroactively when reality returns its verdict — a deal closes, a sprint ships, a payment lands. The result is a continuously growing labeled dataset of decisions tied to outcomes, ready to train domain-specific intuition.

## What It Is Not

- **Not a Q-learning memory system.** We tried Q-values for 8 months. Mean Q-value across 27,000 memories was 0.006; 90% of memories never received any reward signal. Removed on 2026-04-26.
- **Not Mem0 / Zep / Letta.** Those are storage layers. Storage is the easy part — semantic search alone doesn't tell you which memory actually led to a result.
- **Not a replacement for skills or CLAUDE.md.** Those say *how* to do something. OpenExp captures *what happened* and *how it ended*.

## The Methodological Core: No Pre-Labeling

We do **not** hand-craft features at step level (`tone: urgent`, `signal: positive`, `hypothesis: probable`). Pre-labeling injects the labeler's biases and corrupts the eventual training signal. Same hygiene as credit scoring: collect rich features per applicant, label only the terminal outcome (paid / didn't), let the model learn what predicts repayment from data alone.

Only terminal outcomes get labels:

- **`outcome`** — `closed_won` / `closed_lost` / `failed` / `abandoned`
- **`grade`** — `0.0` to `1.0`, school-style

Steps are stored raw. Authors annotate their own intent, hypotheses, and decisions ("I believed X at this point", "I chose Y because Z"). They do not label the *signal quality* of individual events — that's what the eventual model learns.

Casual analogy: kids in school don't get annotations on every homework problem. They turn in work, get a grade at the end of the term, and develop intuition over hundreds of grades.

## Quick Start

```bash
git clone https://github.com/anthroos/openexp.git
cd openexp
./setup.sh
```

That installs the four hooks into Claude Code, brings up Qdrant in Docker, and registers the MCP server.

**Prerequisites:** Python 3.11+, Docker, jq.

No API key required for core functionality. Embeddings run locally via FastEmbed. An Anthropic API key is optional and only powers the two-prompt pipeline (anonymize + extract experience) when you publish.

## How It Works

Four hooks run automatically inside Claude Code:

| Hook | When | What |
|------|------|------|
| `SessionStart` | Session opens | Searches Qdrant for relevant memories, injects top results as context |
| `UserPromptSubmit` | Every message | Lightweight per-prompt recall |
| `PostToolUse` | After Write / Edit / Bash | Captures observations as JSONL |
| `SessionEnd` | Session closes | Ingests transcript into Qdrant; extracts decisions via Opus 4.x (async) |

Retrieval ranks via semantic similarity + BM25 + recency. No magic numbers. No Q-value scoring component.

## The Pipeline

When you decide to publish an experience — turn a real, terminal trajectory into a shareable artifact — two prompts do the work:

1. **`prompts/anonymize.md`** — takes raw trajectory data (transcripts, emails, decisions) and produces an anonymized YAML trajectory. PII is replaced by category tokens (`<counterparty_cto>`, `<regulated_industry>`, `<value:10k-100k>`, `<local_currency>`, `day_+5`) while structural features are preserved. The prompt enforces a reverse-identification rule: tokens narrow enough to identify a counterparty in jurisdiction must be generalized one level up before publication.

2. **`prompts/extract_experience.md`** — reads the anonymized trajectory plus the terminal outcome label and produces a facts-only `meta.yaml` (id, outcome label, duration, step count, category tokens, license). It deliberately refuses to write `applies_when`, `searchable_summary`, or a grade reason — those are interpretations and belong to the reader's Claude at use time, not to the publisher at publish time.

You run both prompts inside your own Claude Code, against your own Qdrant. Nothing is sent to a central server.

## Publishing an Experience

A published experience is four files in a UUID-named directory (schema v3, 2026-04-27):

```
experiences/<uuid>/
├── meta.yaml                    # facts only: id, outcome label, duration, category tokens, license
├── trajectory.anonymized.yaml   # raw ordered timeline of N steps, anonymized
├── README.md                    # human-readable face for the marketplace
└── SKILL.md                     # Claude entry point — read first when skill is invoked
```

`meta.yaml` shape (abridged from seed `d49e0997`):

```yaml
pack:
  id: d49e0997-8455-4d3c-90ca-d6cf54d0f662
  author: ivan-pasichnyk
  license: MIT
  schema_version: 3

  outcome:
    label: closed_won            # fact, not interpretation
    closed_at: day_+57

  duration_days: 57
  step_count: 26

  category_tokens:               # what appears in the trajectory
    - <counterparty_cto>
    - <counterparty_pm>
    - <regulated_industry>
    - <e_signing_platform_local>
    # ...
```

**No `applies_when`, no `searchable_summary`, no `grade_reason`.** Earlier schemas (v2) baked the publisher's read of the timeline into the artifact — one Claude's interpretation, frozen. Schema v3 inverts that: the pack ships raw, and the reader's Claude derives match on the fly against the reader's actual situation. Different readers, different contexts, different inferences from the same trajectory. See `CHANGELOG.md` for the full v2 → v3 transition rationale.

### Install as a Claude Code skill

A published experience is a **namespaced Claude Code skill**:

```
openexp:<author-handle>:<experience-slug>
```

Drop the pack into `~/.claude/skills/openexp:<author>:<slug>/` (rename the directory to the skill-namespaced form on copy). Claude Code auto-discovers it on the next session.

```bash
# Install the seed pack as a skill
cp -r ~/openexp/experiences/d49e0997 \
  ~/.claude/skills/openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot
```

Two layers of identity:
- **Author identity is public** — it signs the pack, like authorship on a research paper.
- **Counterparty identity stays anonymized** — the skill name reveals who created the pack, never who they were dealing with.

`SKILL.md` inside the pack is the entry point — it tells the user's Claude when to invoke, how to use the trajectory, and what not to do (no fabrication, no de-anonymization, attribution required).

See `docs/skill-architecture.md` for the full naming convention, install flow, and design rationale.

The `experiences/` directory in this repo is the seed of an eventual marketplace. The publication format works; seeds will accumulate. A directory of installable experiences is the eventual surface, not a built product today.

## MCP Tools

Five focused tools (hippocampus model — write everything, retrieve selectively):

| Tool | Description |
|------|-------------|
| `search_memory` | Hybrid search: semantic similarity + BM25 + recency |
| `add_memory` | Store a memory. Supports `client_id` for entity tagging |
| `log_prediction` | Log a pack-grounded prediction. Required when an installed experience pack cites a specific `relative_day` as the basis for an action recommendation. |
| `log_outcome` | Resolve a prediction with the observed signal — interpretation-free record. |
| `memory_stats` | Collection stats: point counts by source/type, session count |

### Prediction / outcome instrumentation

Pack-grounded predictions are how the system learns whether a published experience pack actually moves real-world outcomes. Without prediction/outcome pairs, pack value cannot be measured against any baseline, and any future experiment (cross-pack voting, embedding retrieval, new packs from new authors) is unfalsifiable.

**Trigger criterion is sharp.** Logging fires only when the assistant cites a pack's specific `relative_day` as the reason for an action recommendation. No day-citation → no log. Description of a situation without a recommendation → no log. This keeps the dataset honest and the cost low.

**`log_prediction` (new path, schema_version 2)**

| Field | Required | Purpose |
|-------|----------|---------|
| `pack_id` | yes | The pack's slug |
| `pack_author` | yes | Author handle |
| `cited_step` | yes | The exact `day +N` cited |
| `case_id` | yes | External reference (CRM lead_id, ticket ID, deal ID — opaque string) |
| `applied_action` | yes | What was recommended TO do |
| `expected_signal` | yes | Observable resolution |
| `expected_window_days` | yes | Deadline in days for `log_outcome` |
| `prevented_action` | optional | Negative-space prediction — what was recommended NOT to do (often the higher-value half) |
| `notes` | optional | Free-text context |

**`log_outcome` (new path, schema_version 2)**

| Field | Required | Purpose |
|-------|----------|---------|
| `prediction_id` | yes | ID returned from `log_prediction` |
| `actual_signal` | yes | What was observed — raw fact, no interpretation |
| `days_to_resolve` | yes | How many days from prediction to resolution |
| `notes` | optional | Free-text, e.g. unexpected events |

**What's deliberately NOT in the schema:** `confidence` (Claude-side confidence is uncalibrated until ≥30 outcome datapoints), `alternative_action_if_no_pack` and `predicted_outcome_alternative` (the same Claude that writes the prediction would invent the counterfactual, biased toward "the pack helped" — real ablation needs a pack-blind run, separate track).

**Backward compatibility.** The legacy schema (`prediction`, `confidence`, `strategic_value`, `memory_ids_used`) is still accepted by both tools. Calling `log_outcome` with `outcome` + `reward` continues to update Q-values for `memory_ids_used` exactly as before. New-path entries are marked `schema_version: 2` in the JSONL row.

## CLI

```bash
openexp search -q "stalled enterprise procurement" -n 5
openexp ingest          # ingest pending transcripts into Qdrant
openexp stats           # Q-cache + collection stats
```

## Configuration

Environment variables (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant server host |
| `QDRANT_PORT` | `6333` | Qdrant server port |
| `OPENEXP_COLLECTION` | `openexp_memories` | Qdrant collection name |
| `OPENEXP_DATA_DIR` | `~/.openexp/data` | Predictions, retrieval logs |
| `OPENEXP_OBSERVATIONS_DIR` | `~/.openexp/observations` | Hook output |
| `OPENEXP_SESSIONS_DIR` | `~/.openexp/sessions` | Session summaries |
| `OPENEXP_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (local, free) |
| `ANTHROPIC_API_KEY` | *(optional)* | Required only for the publishing pipeline |

## Status

**Pilot. Architecture freeze landed 2026-04-26.** First experience seed published: `experiences/d49e0997/` — a 57-day inbound acquisition that closed at grade 1.0 (author's own assessment), anonymized to category tokens.

Honest about what isn't done:
- The marketplace UI is just a directory in this repo. No web surface yet.
- Anonymization is conservative but not bulletproof for readers with deep domain knowledge.
- Schema may iterate — author-annotation fields (`author_intent`, `author_hypothesis`, `author_decision`) are a likely near-term addition.
- The eventual ML model trained on this corpus does not exist yet. ≥30 graded trajectories first.

See `docs/redesign-2026-04-26.md` for the full architecture freeze and `docs/claude-design-brief.md` for the v2 product framing.

## Contributing

This project is in early stages. See `CONTRIBUTING.md` for setup and workflow.

The most useful contribution right now is **publishing a real experience**. Take one of your own closed trajectories, run it through `prompts/anonymize.md` and `prompts/extract_experience.md`, and open a PR adding a new directory under `experiences/`.

## License

[MIT](LICENSE) &copy; Ivan Pasichnyk

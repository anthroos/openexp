# Publishing an OpenExp Pack

> Each pack is its own GitHub repo. This guide is for authors who want to publish.

## TL;DR

```
github.com/<your-handle>/exp-<slug>/
├── meta.yaml                    # facts only — id, outcome, duration, category tokens
├── trajectory.anonymized.yaml   # raw ordered timeline, anonymized
├── README.md                    # human-readable face for the catalog
└── SKILL.md                     # Claude entry point
```

That's the whole shape. Reference: [`anthroos/exp-inbound-acquisition-with-free-pilot`](https://github.com/anthroos/exp-inbound-acquisition-with-free-pilot).

## Why one repo per pack

The OpenExp engine (`anthroos/openexp`) is the **runtime**. It ships zero packs. Packs live in their own repos so:

1. **Authors own their content.** Issues, license, versioning, attribution — all on the author's repo, not negotiated through the engine.
2. **Lifecycles are independent.** A bug fix to a pack doesn't ship an engine release. An engine refactor doesn't churn pack repos.
3. **Discovery is by `exp-*` repo naming + `openexp.ai` catalog.** A future automated registry index will scan `exp-*` repos under known orgs and aggregate their `meta.yaml` summaries.

## Steps

### 1. Run the publishing pipeline

Inside your own Claude Code session, against your own Qdrant collection. Two prompts:

1. **`prompts/anonymize.md`** — replaces counterparty identity with category tokens (e.g. `<counterparty_cto>`, `<regulated_industry>`). Author identity stays public — it signs the pack.
2. **`prompts/extract_experience.md`** — produces `meta.yaml` (facts only) from the anonymized trajectory plus the terminal outcome label.

Output: four files matching the shape above.

### 2. Create the repo

Repo name: `exp-<slug>` where `<slug>` is the same slug that appears in the install skill name (`openexp:<author>:<slug>`). Public, MIT (or whatever license you declare in `meta.yaml`).

Suggested topics: `openexp`, `openexp-pack`, `experience-pack`, plus domain-specific tags (e.g. `b2b-sales`, `mlops`, `incident-response`).

Suggested description: one sentence — outcome label, duration, category. *"Inbound B2B acquisition trajectory — 57 days, closed_won. An OpenExp experience pack."*

Homepage: `https://openexp.ai/`.

### 3. Push the four files

```bash
git init -b main
git add meta.yaml trajectory.anonymized.yaml README.md SKILL.md
git commit -m "Initial commit — pack format v3"
git remote add origin git@github.com:<your-handle>/exp-<slug>.git
git push -u origin main
```

### 4. Announce

Open an issue on `anthroos/openexp` titled `pack: <your-handle>/<slug>` with a link. The catalog at `openexp.ai/use` is hand-maintained today; the issue is the request to add yours.

## Schema

See `README.md` (the engine repo's, you're reading it from there) section "Publishing an Experience" for the `meta.yaml` shape and the rationale behind shipping raw (no `applies_when`, no summary, no grade reason).

## Anonymization

The hard rule: **never name a real counterparty or platform.** Replace with category tokens:

- People: `<counterparty_cto>`, `<counterparty_pm>`, `<own_cto>`, `<founder>`
- Companies: by category, never by name — `<regulated_industry>`, `<foreign_corp_entity>`
- Tools/platforms: `<e_signing_platform_local>`, `<cloud_provider>`, `<crm_tool>`
- Money: `<local_currency>`, `<central_bank_rate>`
- Geography: `<local_jurisdiction>`, `<host_country>`

A reader with deep domain knowledge may still triangulate identity from the trajectory shape. That's an open problem; the `prompts/anonymize.md` reverse-identification rule (industry / role / geography too narrow → generalize) is the current best-effort defence.

Author identity is public on purpose — like authorship on a research paper. Counterparty identity stays anonymized.

## License

Pack content is under whatever license you declare in `meta.yaml` (default: MIT). Engine code is MIT.

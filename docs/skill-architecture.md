# Skill Architecture — How Experiences Plug into Claude Code

**Status:** Design, accepted 2026-04-26.

OpenExp experiences are distributed and consumed as **Claude Code skills**, using a namespaced naming convention. This document specifies the convention, the filesystem layout, and the install / invoke flow.

## Naming convention

```
openexp:<author-handle>:<experience-slug>
```

| Part | Source | Public? | Example |
|------|--------|---------|---------|
| `openexp` | Plugin namespace, fixed | yes | `openexp` |
| `<author-handle>` | Author's chosen public handle (often GitHub username) | yes | `ivan-pasichnyk` |
| `<experience-slug>` | URL-safe summary of the experience | yes | `inbound-acquisition-with-free-pilot` |

Full example:

```
openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot
```

### Two layers of identity

- **Author identity is public.** It signs the experience pack, like authorship on a research paper. Picking a public handle is part of publishing.
- **Counterparty identity is anonymized.** Trajectory content uses category tokens (`<counterparty_cto>`, `<regulated_industry>`, `<value:10k-100k>`, `day_+5`). The skill name reveals **who created the pack**, never **who they were dealing with**.

## Filesystem layout

A published experience pack ships as a directory containing five files:

```
openexp:<author>:<slug>/
├── SKILL.md                       # Claude entry point — read first when skill is invoked
├── experience.yaml                 # wrapper: applies_when, terminal, searchable_summary
├── trajectory.anonymized.yaml      # ordered timeline of N steps, anonymized
├── steps.indexable.jsonl           # one JSON per line, per-step records for retrieval
└── README.md                       # human-readable face
```

Inside the canonical OpenExp repo, packs live at `experiences/<uuid>/`. The UUID-only directory is the **storage** form. The skill-namespaced directory is the **install** form.

### Storage vs install

| Layer | Path | Purpose |
|-------|------|---------|
| **Storage** | `experiences/<uuid>/` (UUID-only, in repo) | Canonical artifact, version-controlled, anonymized |
| **Install** | `~/.claude/skills/openexp:<author>:<slug>/` | What a user copies into their Claude Code installation |

Reasons for the split:
- UUID is stable and collision-free; it's the canonical ID across all systems.
- Skill-namespaced name is presentation — what users see in their skill list.
- Renaming the slug never breaks the canonical reference.

## Install flow

A user installs a published experience pack by copying the directory into their Claude Code skill directory under the skill-namespaced name:

```bash
# Example: Ivan publishes to a public mirror, user installs.
cd ~/.claude/skills/
git clone https://github.com/anthroos/openexp-experience-ivan-acquisition \
  openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot
```

Or, if the canonical pack lives in this repo and the user wants the seed:

```bash
cp -r ~/openexp/experiences/d49e0997 \
  ~/.claude/skills/openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot
```

Claude Code auto-discovers the skill on next session start. No registration step.

## Invocation flow

When a user is working on a task that matches a pack's `applies_when`, two paths surface the pack:

1. **Auto-surface via search.** The hooks injected by OpenExp run `search_memory` against the user's query. Step-level records (`steps.indexable.jsonl` content) are indexed in the user's local Qdrant when they install the pack. Pattern queries pull matching steps; the parent SKILL.md gets pulled with the step context.

2. **Explicit invocation.** The user types `/openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot` (or whatever invocation surface Claude Code exposes for namespaced skills) when they know they want this specific pack.

Either way, the user's Claude reads `SKILL.md` first to understand:
- when to invoke (constraints in "When to invoke")
- how to use the pack (where each file fits)
- what not to do (attribution, no fabrication, no de-anonymization)

The pack content is then context for the user's session, not the user's Claude's own analysis.

## Authorship and attribution

Every pack carries the author's handle in the skill name and a short bio in `SKILL.md`. When the user's Claude surfaces material from the pack, attribution is required:

- ✓ "On day +25 of Ivan's trajectory, the counterparty went silent..."
- ✓ "Ivan Pasichnyk's pack describes a moment where..."
- ✗ "Based on common patterns, after a free pilot the counterparty often..."

The reader's Claude does not own the experience — it is borrowed and cited.

## Versioning

A pack's content can be revised. The UUID stays. The slug may stay or change. A revision should:

- Update `metadata.created_at` → keep original; add `metadata.updated_at`.
- Add an optional `metadata.version` field if changes are substantial.
- Keep `trajectory.anonymized.yaml` immutable for old trajectory steps; add new steps if the trajectory was reopened (rare).

## Why skills, not a custom protocol

- Skill discovery is already in Claude Code; reusing it avoids a parallel installation and registration pipeline.
- `SKILL.md` is the natural place to specify "when to invoke" and "what not to do" — these are LLM instructions, exactly the file's purpose.
- Namespacing (`openexp:author:slug`) lets multiple packs by multiple authors coexist without naming conflicts.

## Open questions

- Trust and verification — `metadata.verified: false` is the default. A future tier where the OpenExp project audits a pack and signs it is intended but not built.
- Pack search across the network — for now, packs are discovered through the repo, social shares, and direct links. A central index is downstream.
- Conflict between packs — if two packs claim to apply to the same situation, the user's Claude follows the one whose `applies_when` matches more tightly. No ranking infrastructure today.

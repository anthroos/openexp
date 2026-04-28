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
- **Counterparty identity is anonymized.** Trajectory content uses category tokens (`<counterparty_cto>`, `<regulated_industry>`, `<value:10k-100k>`, `day_+5`). The skill name reveals **who created the pack**, never **who they were dealing with**. See `prompts/anonymize.md` for the reverse-identification rule that keeps tokens broad enough to be safe.

## Filesystem layout

A published experience pack ships as a directory containing four files:

```
openexp:<author>:<slug>/
├── SKILL.md                       # Claude entry point — read first when skill is invoked
├── meta.yaml                       # facts only: id, outcome label, duration, category tokens, license
├── trajectory.anonymized.yaml      # ordered timeline of N raw steps, anonymized
└── README.md                       # human-readable face
```

**Why no `experience.yaml`?** Earlier schemas (v2) shipped a wrapper artifact with `applies_when`, `searchable_summary`, and `grade_reason`. Those fields baked the publisher's read of the timeline into the pack — one Claude's interpretation, frozen at publish time. Schema v3 (2026-04-27) drops that wrapper and ships **raw**: `meta.yaml` carries facts only (outcome label, category tokens, structural counts), and the reader's Claude derives `applies_when` on the fly against the reader's actual situation. Different readers, different contexts, different inferences from the same trajectory.

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

1. **Skill-list discovery.** Claude Code auto-discovers the namespaced skill directory and lists it among available skills. The user references the pack by name, by tag, or by describing a situation that matches its `applies_when` — Claude reads the pack files into context. **Auto-firing on situation patterns alone, without the user naming the pack, is on the roadmap, not shipped.** Embedding-based retrieval over per-step records is also future work — today, matching is read-time, not search-time.

2. **Explicit invocation.** The user types `/openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot` (or whatever invocation surface Claude Code exposes for namespaced skills) when they know they want this specific pack.

Either way, the user's Claude reads `SKILL.md` first to understand:
- when to invoke (constraints in "When to invoke")
- how to use the pack (where each file fits)
- what not to do (attribution, no fabrication, no de-anonymization)
- **how to reply** (default quiet mode vs verbose on explicit ask)

The pack content is then context for the user's session, not the user's Claude's own analysis.

### SKILL.md template

A canonical `SKILL.md` for a new pack lives at [`templates/SKILL.template.md`](../templates/SKILL.template.md). Copy it, fill the placeholders, ship. The template carries the **default quiet output mode** — without it the user's Claude tends to dump the entire trajectory back into chat on every reply, which destroys the conversational register. Keep the "Output style" and "Verbose mode" sections verbatim unless you have a specific reason to override them.

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

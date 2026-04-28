# Experience d49e0997 — Inbound Acquisition Trajectory

**Author:** Ivan Pasichnyk (`ivan-pasichnyk`)
**Outcome label:** `closed_won` at `day_+57`
**Duration:** 57 days · 26 steps
**License:** MIT

## What is published

A 57-day inbound B2B acquisition arc, anonymized step by step. The pack ships **raw**: the timeline as it happened, plus a facts-only `meta.yaml`. There is **no `applies_when`**, **no summary**, **no author grade reason**. Those are interpretations and are deliberately omitted — the reader's Claude derives match from the trajectory on the fly.

## Files

- `meta.yaml` — facts only (id, outcome label, duration, category tokens, license)
- `trajectory.anonymized.yaml` — the timeline (26 ordered steps from `relative_day: 0` to `relative_day: +57`)
- `SKILL.md` — Claude entry point

Counterparty identity replaced with category tokens (`<counterparty_cto>`, `<regulated_industry>`, `<e_signing_platform_local>`, `<local_currency>`, etc.). Author identity is public — it signs the pack.

## How to install

```bash
git clone https://github.com/anthroos/openexp.git
cp -r openexp/experiences/d49e0997 \
  ~/.claude/skills/openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot/
```

Auto-discovered on next Claude Code session. To use the pack, install the `openexp-use` Claude Code skill alongside it (one-time setup, applies to all packs):

```bash
git clone https://github.com/anthroos/claude-skills.git
ln -s ~/claude-skills/skills/core/openexp-use ~/.claude/skills/openexp-use
```

Then describe your situation in your own words — `openexp-use` will discover this pack, read its trajectory, and reply with a cited `relative_day`.

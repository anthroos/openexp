# ADR 0001 — v4 schema: operational log + dependency manifest

**Status:** Proposed
**Date:** 2026-05-03
**Author:** Ivan Pasichnyk + AI assistant
**Driven by:** review of pack-as-replay vs pack-as-narrative gap, sparked while publishing the 5th pack (`setting-up-google-analytics-on-a-static-site`)

## Context

Pack format v3 stores the trajectory as ordered `step` records with:

- `relative_day` (integer)
- `kind` (message | decision | action | observation)
- `channel` (chat | shell | gh | github_issue | document | web_console)
- `actors[]` with `role` + `seniority`
- `content` (prose description)

Plus pack-level metadata: `outcome.label`, `duration_days`, `step_count`, `category_tokens`.

This is sufficient for **narrative replay** — a reader's Claude can read the timeline and ground its advice in real events. It is **not sufficient for operational replay** — the reader cannot reproduce the work because:

1. **Tools are not named.** A step says "AI inserts gtag snippet into 6 HTML files" — but the reader does not know if that was the `Edit` tool, a `Write`, a `sed`, a build step. If the reader's environment has only `sed`, the cited day is decorative.

2. **Skills are not named.** A step says "AI runs scrub" — but the reader does not know that this was the `openexp-pack-scrub` Claude Code skill. If the reader does not have it installed, the citation is unreachable.

3. **Commands are not preserved.** A step says "AI verifies the live site with curl + grep" — but the actual command (`curl -sS https://example.tld/ | grep -E "..."`) is gone. The reader has to re-derive it, and the re-derivation is exactly what the pack was supposed to save them from.

4. **Prerequisites are not enumerated.** The reader has no list of "you need `gh` CLI installed, a Firebase Hosting project linked, a GA4 account" before they can replay anything. The pack assumes the reader already has the setup.

Without these four, packs are **not falsifiable** in any operational sense — they are war stories with timestamps, not reproducible trajectories. This contradicts the OpenExp design principle of "the reader's Claude derives match from the trajectory on the fly" — derivation requires more than prose.

## Decision

Introduce schema v4 with two additions: per-step **operational log** and per-pack **dependency manifest**.

### Per-step additions (trajectory.anonymized.yaml)

```yaml
- relative_day: 0
  kind: action
  channel: shell
  actors:
    - role: own_ai_assistant
      seniority: senior

  # NEW v4 fields — all optional in v3 packs, required in v4 action/shell steps
  tool: Bash | Edit | Read | Write | Skill | WebFetch | <other>
  skill_invoked: openexp-pack-scrub        # only if tool == Skill
  commands:                                # only if tool == Bash; anonymized
    - "grep -nE '<measurement_id_pattern>' <pack_files>"
  files_touched:                           # only if tool ∈ {Edit, Write, Read}
    - <site_repo>/index.html
  timestamp_offset: 1247                   # seconds from session start, optional

  content: "AI runs scrub greps across 4 public files — 13 categories."
```

**Rules:**
- `tool` is required for `kind: action` and `channel: shell`. For `kind: message|decision`, optional.
- `skill_invoked` only when `tool: Skill`. Names a Claude Code skill in the registry.
- `commands` is anonymized: literal paths (`/Users/<author>/...`) become `<home_dir>`, real IDs become tokens, real domains become `<site_url>`-class tokens. Same redaction rules as `content`.
- `files_touched` is similarly anonymized.
- `timestamp_offset` is optional and is the seconds from session start — useful for deriving sub-day ordering when the list-position heuristic is ambiguous.

### Per-pack additions (meta.yaml)

```yaml
pack:
  id: <UUID>
  schema_version: 4                        # bumped from 3

  # ... existing fields (author, license, outcome, duration_days, step_count, category_tokens) ...

  dependencies:
    runtime:                               # to REPLAY the trajectory
      cli_tools:
        - name: gh
          install: "brew install gh"
          docs: "https://cli.github.com/"
        - name: firebase
          install: "npm install -g firebase-tools"
          docs: "https://firebase.google.com/docs/cli"
      external_accounts:
        - "Google Analytics 4 account (web access)"
        - "Firebase Hosting project linked to your domain"
        - "GitHub repository for the static site"
      claude_skills:
        - name: openexp-use
          source: "https://github.com/anthroos/claude-skills/tree/main/skills/core/openexp-use"
          install_cmd: "ln -s ~/claude-skills/skills/core/openexp-use ~/.claude/skills/openexp-use"

    authoring:                              # only needed if you want to PACKAGE a similar arc into a pack
      claude_skills:
        - name: openexp-pack-author
          source: "https://github.com/anthroos/claude-skills/tree/main/skills/core/openexp-pack-author"
        - name: openexp-pack-scrub
          source: "https://github.com/anthroos/claude-skills/tree/main/skills/core/openexp-pack-scrub"
      hooks:
        - name: PostToolUse observation capture
          part_of: "openexp engine — https://github.com/anthroos/openexp"
```

**Rules:**
- `dependencies.runtime` is **required** in v4. Even an empty list (`runtime: { cli_tools: [], external_accounts: [], claude_skills: [] }`) is allowed but should be honest — if the trajectory had no external dependencies, write so explicitly.
- `dependencies.authoring` is **optional**. Only present if pack author wants to surface what was needed to *create* the pack (separate from what's needed to *follow* it).
- Anonymization rule: dependencies live at the *category* level, not literal — "GitHub repository for a static site" is fine, "anthroos/exp-foo" is not (unless the repo is the pack itself).

### Minimum bar for "v4-compliant"

A v4 pack MUST have:
1. `meta.yaml.pack.schema_version: 4`
2. `meta.yaml.pack.dependencies.runtime` (even if all sub-lists are empty)
3. Each `kind: action` step with `channel: shell` MUST have `tool` field
4. Each `kind: action` step with `tool: Skill` MUST have `skill_invoked` field

Backwards compatibility:
- `openexp-use` (the runtime applier) reads both v3 and v4. v3 packs continue to work — they just lack the operational layer for replay.
- v4 packs cannot be parsed by tooling that hardcodes v3 schema. That's acceptable because nothing currently does (v3 is YAML and tolerant; new fields are additions, not renames).

## Consequences

### Positive

- **Replayability.** A reader who matches a step can run the same `commands` (after their local de-anonymization) — the cited day becomes operational, not decorative.
- **Falsifiable predictions get richer.** When `log_prediction` fires on a cited step, it can include "expected tool: Bash" or "expected skill: openexp-pack-scrub" — turning the prediction into a verifiable claim about the reader's environment, not just the outcome.
- **Bot-to-bot use case becomes real.** The "agent → agent" mode on the openexp.ai homepage is currently aspirational because v3 packs don't carry enough for an agent to replicate. v4 closes that gap.
- **Onboarding for non-author readers.** The `dependencies.runtime` block is the first thing a reader sees — no more "I installed the pack but my Claude has nothing to do with `gh repo create` because `gh` is not installed".

### Negative

- **Anonymization surface grows.** Commands and file paths are PII-rich (literal `/Users/<author>/`, real domains, real repo names). The scrub skill needs new categories. New leaks become possible in `commands` and `files_touched` fields.
- **Pack files get larger.** Trajectory length grows ~2× when every action step carries tool + commands + files. For a 50-step pack, that's a real readability hit.
- **Schema drift risk.** Some packs will be v3, some v4 — the catalog page on openexp.ai already pretends they're peers. We need either visible schema-version badges, or a soft commitment to backfill all packs to v4 within N weeks of v4 landing.
- **Authoring time grows.** Pack-author skill needs Step 1.5 ("extract operational log from observations") and Step 4 needs to query CLI installation paths to populate `dependencies.runtime`. ~15-20 min added per pack.

### Backfill policy

For the 5 existing v3 packs in catalog as of 2026-05-03:

- **`setting-up-google-analytics-on-a-static-site`** (just published today) — observations still in `~/.claude-memory/observations/`. Backfill to v4 cleanly.
- **`publishing-an-openexp-pack`** — published 2026-05-03 morning. Observations present. Backfill.
- **`inbound-acquisition-with-free-pilot`**, **`inbound-acquisition-with-letterhead-pivot`**, **`referral-acquisition-with-paid-pilot-to-large-contract`** — these are real B2B acquisition arcs spanning 57–72 days each. The trajectories were extracted from CRM activities + email threads, not from a single Claude Code session. They DO NOT have a tool/skill log for most steps because most steps were human-authored emails and meetings, not automated tool calls. For these:
  - Mark `schema_version: 4`
  - Provide `dependencies.runtime` (CRM access, email client, e-signing platform, etc. — at category-token level)
  - Skip per-step `tool` field where the step was a human action with no Claude Code tool involved (allowed by spec — `tool` is required only for `channel: shell` steps)
  - This honest minimal upgrade is ~15 min per pack, not 30+

### Rollout

1. Land this ADR (current step).
2. Update `~/openexp/prompts/anonymize.md` and `~/openexp/prompts/extract_experience.md` to handle the new fields and their anonymization rules.
3. Update `~/.claude/skills/openexp-pack-author/SKILL.md` with Step 1.5 (observations → operational log extraction) and updated Step 2 / Step 4 schema.
4. Update `~/.claude/skills/openexp-pack-scrub/SKILL.md` with new scrub categories for `commands` and `files_touched`.
5. Backfill the 5 existing packs to v4. Push as new commits to each `exp-<slug>` repo. Bump `schema_version` to 4.
6. Update `openexp-use` skill to handle v4 (read new fields if present, fall back to v3 if not).
7. Update site copy on openexp.ai/about to reflect v4 (the "Why raw" section already aligns; just mention v4 alongside).

Steps 1–5 are reversible (everything is git-tracked). Step 6 is forward-compatible (v3 keeps working). Step 7 is text only.

## Open questions (deferred, not blocking)

1. **Should `commands` be the literal command (after anonymization) or a shape (`grep <pattern> <files>`)?**  Literal is more useful for replay; shape is more concise. Initial recommendation: **literal**, anonymized via the same rules as `content`.

2. **Should `tool` be the Claude Code tool name (`Bash`, `Edit`) or the underlying CLI (`grep`, `firebase`)?**  Claude Code tool name keeps it portable across versions; the underlying CLI lives in `commands[]`. Initial recommendation: **Claude Code tool name** in `tool`, underlying CLI in `commands[]`.

3. **Should `dependencies.runtime` be machine-checkable (e.g. install via `npm install` from a generated package.json analogue)?**  Tempting but premature. Different OSes have different installers. Initial recommendation: **plain prose** for v4, machine-checkable as a future addition once we have ≥10 packs and can see what categories repeat.

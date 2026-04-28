# Changelog

All notable changes to OpenExp.

## 2026-04-27 — Pack format v3 (raw publication)

**Drop publisher-side interpretation. Ship raw, derive on read.**

Schema v2 shipped each pack as five files including `experience.yaml` — a wrapper artifact carrying `applies_when`, `searchable_summary`, and `grade_reason`. Those fields baked one Claude's read of the timeline into the artifact at publish time. Different readers with different contexts inherit the publisher's bias.

Schema v3 inverts that:

- **`experience.yaml` removed.** No `applies_when`, no `searchable_summary`, no `grade_reason`.
- **`meta.yaml` added.** Facts only: pack id, author handle, license, outcome label (`closed_won` / etc.), `closed_at` day, `duration_days`, `step_count`, `category_tokens` list. Nothing the publisher *interpreted*.
- **`trajectory.anonymized.yaml` is the canonical artifact.** It already carries the timeline; v3 makes it the only source of truth on what happened.
- **The reader's Claude derives match on the fly.** Reads `meta.yaml` for filtering, reads `trajectory.anonymized.yaml` for step content, decides whether the trajectory's shape applies to the user's actual situation. The pack does not pre-declare when it applies.

Files changed in this release:

- `experiences/d49e0997/experience.yaml` — **deleted**.
- `experiences/d49e0997/meta.yaml` — **new**, schema v3.
- `experiences/d49e0997/trajectory.anonymized.yaml` — top-level `experience_type` / `domain` / `duration_days` / `notes` block removed; only `trajectory.steps` remains. The narrative `notes` was author interpretation; structural facts moved into `meta.yaml`.
- `experiences/d49e0997/SKILL.md` — rewritten "How to use" around `meta.yaml` + raw trajectory; explicit "Why raw" section explaining the inversion.
- `experiences/d49e0997/README.md` — rewritten human face: outcome label as fact, no grade quote.
- `templates/SKILL.template.md` — pack contents table updated to 4 files (meta + trajectory + README + SKILL); "How the pack informs your replies" now describes derive-on-read.
- `prompts/extract_experience.md` — repurposed to emit `meta.yaml` only. Refuses to write `applies_when`, `searchable_summary`, `grade`, `grade_reason` even if author tries to supply them.
- `prompts/anonymize.md` — added "Reverse-identification rule": when an industry/role/geography combination has fewer than ~100 plausible matches in jurisdiction, generalize one level up (e.g. `<regulated_industry>` → `<regulated_industry>`). Strip identifying free-text from `content`, do not just rename tokens.
- `docs/skill-architecture.md` — pack layout updated; "Why no `experience.yaml`?" section explains v2 → v3 transition.

### Privacy fix bundled in this release

The seed pack's `<regulated_industry>` token plus a free-text phrase ("operating in regulated environments") in step 0 narrowed the discrimination set enough to be reverse-identifiable in a small jurisdiction. Both replaced — token generalized to `<regulated_industry>`, identifying phrase stripped. The new "Reverse-identification rule" in `anonymize.md` is intended to prevent future packs from re-introducing the same class of leak.

### Breaking change

- Existing v2 packs (`experience.yaml` present) still work for read because the SKILL.md tells Claude what files to read; it just gets the four-file v3 layout going forward. New packs published from this commit onward are v3 only.

---

## 2026-04-27 — Pack-grounded prediction/outcome schema

**`log_prediction` / `log_outcome` MCP tools — new schema with backward compat.**

Pack-grounded predictions now carry their own minimal schema. The old free-text `prediction` + `confidence` + `reward` shape is deprecated but still accepted, so existing callers don't break.

### `log_prediction` (new path)

Required:

- `pack_id` — pack slug
- `pack_author` — author handle
- `cited_step` — the exact `day +N` cited as the basis for the recommendation
- `case_id` — external reference (CRM lead_id, ticket ID, deal ID)
- `applied_action` — what was recommended TO do
- `expected_signal` — observable resolution
- `expected_window_days` — deadline in days

Optional: `prevented_action`, `notes`.

Removed from required schema: `confidence`, `strategic_value`. Reasoning: Claude-side confidence is uncalibrated until we have ≥30 outcome datapoints to anchor against. Without baseline, those numbers are noise. Will be reintroduced when calibration is possible.

Not introduced: `alternative_action_if_no_pack`, `predicted_outcome_alternative`. Reasoning: the same Claude that writes the prediction would invent the counterfactual — biased toward "the pack helped". Real ablation requires a separate pack-blind run, which is a different track.

### `log_outcome` (new path)

Required:

- `prediction_id`
- `actual_signal` — raw observation, no interpretation
- `days_to_resolve`

Optional: `notes`.

`reward` and `cause_category` removed from required schema. The new path is observation-only — interpretation happens later, in aggregate, with the right baselines.

### Backward compatibility

- Existing predictions stored under the old schema (`prediction`, `confidence`, `strategic_value`, `memory_ids_used`) remain readable; nothing is migrated or rewritten.
- Both `log_prediction` and `log_outcome` accept the legacy fields. Calling them with `outcome` + `reward` keeps updating Q-values for `memory_ids_used`, exactly as before. Calling the new path skips Q-update entirely (no reward — no propagation).
- Schema version is now `2` on new entries (`schema_version: 2` in the JSONL row).

### Pack instrumentation rule

A new "Instrumentation (mandatory)" section was added to:

- `experiences/d49e0997/SKILL.md` — the seed pack (Ivan Pasichnyk's inbound acquisition pack)
- `templates/SKILL.template.md` — canonical template for new packs

The rule: when a pack cites a specific `relative_day` as the reason for a real-world action recommendation, the assistant must immediately call `log_prediction` with the new-path fields, then `log_outcome` once the window passes.

Trigger criterion is sharp: if no `relative_day` is cited, no log fires. If the assistant only describes a situation without recommending, no log fires. This keeps the dataset clean and the cost low.

### Why now

The seed pack went into production for the first time on 2026-04-27. Without prediction/outcome instrumentation, pack value cannot be measured against any baseline, and any future experiment (cross-pack voting, embedding-based retrieval over per-step records, new packs from other authors) is unfalsifiable. This commit closes that gap.

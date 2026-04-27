# Changelog

All notable changes to OpenExp.

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

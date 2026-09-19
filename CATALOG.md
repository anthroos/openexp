# Experience Pack Catalog

Published packs, installable as namespaced Claude Code skills
(`openexp:<author>:<slug>` — see [README](README.md#installing-an-experience)).
Each pack ships facts-only (schema v3): anonymized trajectory + terminal outcome
label, no author interpretation.

| Pack | Author | Outcome | Duration | Repo |
|---|---|---|---|---|
| inbound-acquisition-with-free-pilot | ivan-pasichnyk | `closed_won` at day +57 | 57 days, 26 steps | [exp-inbound-acquisition-with-free-pilot](https://github.com/anthroos/exp-inbound-acquisition-with-free-pilot) |
| referral-acquisition-with-paid-pilot-to-large-contract | ivan-pasichnyk | `engaged` at day +65 | 65 days | [exp-referral-acquisition-with-paid-pilot-to-large-contract](https://github.com/anthroos/exp-referral-acquisition-with-paid-pilot-to-large-contract) |
| inbound-acquisition-with-letterhead-pivot | ivan-pasichnyk | `engaged` (MNDA executed) at day +72 | 72 days | [exp-inbound-acquisition-with-letterhead-pivot](https://github.com/anthroos/exp-inbound-acquisition-with-letterhead-pivot) |
| setting-up-google-analytics-on-a-static-site | ivan-pasichnyk | `ga_configured` at day +0 | sub-day, 15 steps | [exp-setting-up-google-analytics-on-a-static-site](https://github.com/anthroos/exp-setting-up-google-analytics-on-a-static-site) |
| publishing-an-openexp-pack | ivan-pasichnyk | `pack_published` at day +0 | sub-day | [exp-publishing-an-openexp-pack](https://github.com/anthroos/exp-publishing-an-openexp-pack) |

The seed pack (`experiences/d49e0997/`) in this repo is
`inbound-acquisition-with-free-pilot`.

**Adding a pack:** publish your `exp-<slug>` repo, then open an issue here
titled `pack: <author>/<slug>` with repo link, slug, outcome label and duration.

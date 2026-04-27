"""Retrospective seed: datapoint #1 for the inbound-acquisition pack.

Logs the first counterparty case as the first prediction/outcome pair for
the seed pack `openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot`.
Both calls are made after the fact — pack was installed and applied on
2026-04-27 evening; outcome was observable within days.

Run once:

    .venv/bin/python3 scripts/seed_first_outcome.py

Idempotency: re-runs append a fresh prediction/outcome pair, they do not
deduplicate. If you mis-run, manually trim the resulting line from
~/.openexp/data/predictions.jsonl and outcomes.jsonl.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Resolve OpenExp data dir the same way the runtime does.
DATA_DIR = Path(os.environ.get("OPENEXP_DATA_DIR", str(Path.home() / ".openexp" / "data")))


def main() -> int:
    # Lazy import so the script doesn't pay startup cost if invoked with --help-style noop.
    from openexp.reward_tracker import RewardTracker

    tracker = RewardTracker(data_dir=DATA_DIR, experience="default")

    pid = tracker.log_prediction(
        pack_id="inbound-acquisition-with-free-pilot",
        pack_author="ivan-pasichnyk",
        cited_step="day +57",
        case_id="<COUNTERPARTY_CASE_ID>",
        applied_action=(
            "Upload contract to local e-signing platform, sign with "
            "founder's digital signing key, send invite to counterparty PM."
        ),
        prevented_action=(
            "Send a follow-up nudge during the 6-day pause that had accumulated "
            "before the upload step."
        ),
        expected_signal="counterparty signs both sides via <E_SIGNING_PLATFORM>",
        expected_window_days=7,
        notes=(
            "First production application of this pack. Pack installed "
            "2026-04-27 16:54, applied same evening. Both prediction and "
            "outcome are logged retrospectively — datapoint #1, not a live "
            "calibration."
        ),
    )
    print(f"prediction_id: {pid}")

    result = tracker.log_outcome(
        prediction_id=pid,
        actual_signal="signed both sides via <E_SIGNING_PLATFORM>",
        days_to_resolve=4,
        notes=(
            "Counterparty PM signed within 4 days of founder's digital signing key signature. "
            "Twin trajectory in the pack closed in ~90 minutes — slower here "
            "but well within the 7-day window."
        ),
    )
    print("outcome:", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())

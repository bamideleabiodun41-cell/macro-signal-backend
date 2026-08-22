"""
Release Scheduler
-------------------
Watches the calendar feed and drives two automatic transitions per
indicator, with no manual trigger needed:

    pending -> published   (fires exactly once, ~24h before release_at)
    published -> released  (fires exactly once, right after release_at passes)

This is what turns "publish 24h before" and "flag when it's actually
out" from manual API calls into something that just runs.

Designed to be called from api_server.py's background loop rather than
run as a separate process, so it shares the same predictions file and
rescore functions without needing to make HTTP calls to itself.
"""

import json
import os
from datetime import datetime, timezone

import signal_engine

PREDICTIONS_FILE = "active_predictions.json"
PUBLISH_WINDOW_HOURS = 24


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def run_scheduler_tick(release_schedule, rescore_functions):
    """
    One check cycle. Call this on a loop (every few minutes is plenty —
    this doesn't need 10-second precision, only the news/UI refresh does).

    release_schedule: {indicator: iso_timestamp} from calendar_feed.py
    rescore_functions: same RESCORE_FUNCTIONS dict used by revision_engine
    """
    now = datetime.now(timezone.utc)
    predictions = load_json(PREDICTIONS_FILE, [])
    predictions_by_indicator = {p["indicator"]: p for p in predictions}
    changed = False

    for indicator, release_at_str in release_schedule.items():
        if not release_at_str:
            continue
        release_at = datetime.fromisoformat(release_at_str)
        hours_until_release = (release_at - now).total_seconds() / 3600

        existing = predictions_by_indicator.get(indicator)
        existing_status = existing.get("status") if existing else None

        # --- Transition 1: pending -> published, ~24h before release ---
        needs_publish = existing_status not in ("published", "released")
        if needs_publish and 0 <= hours_until_release <= PUBLISH_WINDOW_HOURS:
            rescore_fn = rescore_functions.get(indicator)
            if not rescore_fn:
                continue
            try:
                fresh = rescore_fn()
            except Exception as e:
                print(f"[scheduler] failed to build {indicator} prediction: {e}")
                continue

            fresh["release_at"] = release_at_str
            fresh["status"] = "published"
            fresh["published_at"] = now.isoformat()
            fresh = signal_engine.attach_trade_signals(fresh)
            predictions_by_indicator[indicator] = fresh
            changed = True
            print(f"[scheduler] published {indicator} prediction ({hours_until_release:.1f}h before release)")

        # --- Transition 2: published -> released, right after release_at passes ---
        elif existing_status == "published" and now >= release_at:
            existing["status"] = "released"
            existing["released_at"] = now.isoformat()
            changed = True
            print(f"[scheduler] marked {indicator} as released")

    if changed:
        save_json(PREDICTIONS_FILE, list(predictions_by_indicator.values()))

    return changed


if __name__ == "__main__":
    # Manual test — in production this is called from api_server.py's
    # background loop, sharing the real release_schedule and rescore fns.
    import calendar_feed
    schedule = calendar_feed.build_release_schedule()
    print("Testing scheduler tick with live calendar (no rescore functions wired in this standalone run)")
    run_scheduler_tick(schedule, rescore_functions={})

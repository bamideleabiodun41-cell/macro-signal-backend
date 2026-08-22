"""
Performance Tracker
---------------------
Once a prediction is marked "released" (by scheduler.py), this pulls
the actual reported number from FRED and compares it against what was
predicted — this is the data source for the "Prediction Performance"
page and the accuracy history shown on prediction cards.

Run this on a delay after each release (actual data usually posts to
FRED within minutes to a few hours of the official release) — a
scheduled check a few hours after release_at is more reliable than
checking instantly.
"""

import os
import json
import requests
from datetime import datetime, timezone

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_KEY_HERE")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

PREDICTIONS_FILE = "active_predictions.json"
PERFORMANCE_LOG_FILE = "performance_log.json"

# Maps each indicator to the actual-data FRED series to check against.
# GBP CPI / AUD Employment need their own actual-data sources (ONS/ABS)
# — not wired here yet since those APIs return actuals differently.
ACTUAL_DATA_SERIES = {
    "NFP": "PAYEMS",
    "CPI": "CPIAUCSL",
    "PPI": "PPIFIS",
    "GDP": "GDP",
    "PCE": "PCEPI",
}


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def fetch_latest_actual(series_id):
    params = {
        "series_id": series_id, "api_key": FRED_API_KEY,
        "file_type": "json", "sort_order": "desc", "limit": 2,
    }
    resp = requests.get(FRED_BASE, params=params, timeout=10)
    resp.raise_for_status()
    obs = [(o["date"], float(o["value"])) for o in resp.json()["observations"] if o["value"] != "."]
    if len(obs) < 2:
        return None
    latest, prior = obs[0][1], obs[1][1]
    return {"date": obs[0][0], "level": latest, "change": latest - prior}


def evaluate_prediction(prediction, actual):
    """
    Checks whether the actual value fell inside the predicted range,
    and whether the directional verdict (hot/soft/in-line) matched
    the actual direction of surprise.
    """
    low, high = prediction["range"]
    actual_value = actual["change"]
    in_range = low <= actual_value <= high
    midpoint = (low + high) / 2

    if prediction["verdict"] == "hot":
        direction_correct = actual_value >= midpoint
    elif prediction["verdict"] == "soft":
        direction_correct = actual_value <= midpoint
    else:
        direction_correct = in_range

    return {
        "in_range": in_range,
        "direction_correct": direction_correct,
        "predicted_range": [low, high],
        "predicted_base_case": prediction["base_case"],
        "actual_value": actual_value,
        "actual_date": actual["date"],
    }


def check_released_predictions():
    """
    Scans active_predictions.json for anything marked 'released' that
    hasn't been scored yet, pulls the actual, evaluates, and logs it.
    """
    predictions = load_json(PREDICTIONS_FILE, [])
    performance_log = load_json(PERFORMANCE_LOG_FILE, [])
    already_scored = {(e["indicator"], e["release_at"]) for e in performance_log}

    newly_scored = []

    for prediction in predictions:
        if prediction.get("status") != "released":
            continue

        key = (prediction["indicator"], prediction["release_at"])
        if key in already_scored:
            continue

        series_id = ACTUAL_DATA_SERIES.get(prediction["indicator"])
        if not series_id:
            continue

        try:
            actual = fetch_latest_actual(series_id)
        except Exception as e:
            print(f"[performance] failed to fetch actual for {prediction['indicator']}: {e}")
            continue

        if not actual:
            continue

        evaluation = evaluate_prediction(prediction, actual)
        entry = {
            "indicator": prediction["indicator"],
            "release_at": prediction["release_at"],
            "scored_at": datetime.now(timezone.utc).isoformat(),
            **evaluation,
        }
        performance_log.append(entry)
        newly_scored.append(entry)
        print(f"[performance] {prediction['indicator']}: "
              f"in_range={evaluation['in_range']}, direction_correct={evaluation['direction_correct']}")

    if newly_scored:
        save_json(PERFORMANCE_LOG_FILE, performance_log)

    return newly_scored


def get_accuracy_summary():
    """Aggregate hit rate, overall and per-indicator — feeds the Performance page."""
    log = load_json(PERFORMANCE_LOG_FILE, [])
    if not log:
        return {"overall": None, "by_indicator": {}}

    overall_correct = sum(1 for e in log if e["direction_correct"])
    by_indicator = {}
    for indicator in set(e["indicator"] for e in log):
        entries = [e for e in log if e["indicator"] == indicator]
        correct = sum(1 for e in entries if e["direction_correct"])
        by_indicator[indicator] = {"correct": correct, "total": len(entries)}

    return {
        "overall": {"correct": overall_correct, "total": len(log)},
        "by_indicator": by_indicator,
    }


if __name__ == "__main__":
    scored = check_released_predictions()
    print(f"Scored {len(scored)} newly released prediction(s)")
    print(json.dumps(get_accuracy_summary(), indent=2))

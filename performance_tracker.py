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
# GBP CPI and AUD Employment now included — both sourced via FRED's
# mirror of OECD data, avoiding the ONS/ABS APIs that proved broken
# or awkward to use directly.
ACTUAL_DATA_SERIES = {
    "NFP": "PAYEMS",
    "CPI": "CPIAUCSL",
    "PPI": "PPIFIS",
    "GDP": "GDP",
    "PCE": "PCEPI",
    "GBP CPI": "GBRCPIALLMINMEI",       # UK CPI, OECD-sourced via FRED
    "AUD Employment": "LRHUTTTTAUM156S", # Australia unemployment RATE (not a jobs-count level)
}

# AUD Employment's only clean FRED-available actual is unemployment
# rate, not an employment-count level like the prediction range uses.
# Different units mean it can't use the normal range-containment check
# — it gets a dedicated directional evaluator instead (see below).
INVERSE_RATE_INDICATORS = {"AUD Employment"}

# How to interpret "change" for each indicator's actual value, so it
# matches the units the predictor's range is actually expressed in.
# CPI/PPI/PCE/GBP CPI predict a MoM PERCENT change (e.g. "0.2 to 0.4"
# means 0.2%-0.4%), but FRED's raw index level difference is in INDEX
# POINTS, not percent — comparing those directly was a real unit bug.
# NFP predicts a raw jobs-count level change, which matches FRED's
# level difference correctly as-is, so it stays untouched.
VALUE_TYPE = {
    "NFP": "level_change",
    "CPI": "percent_change",
    "PPI": "percent_change",
    "PCE": "percent_change",
    "GBP CPI": "percent_change",
    "GDP": "level_change",  # separate known issue (annualized rate vs raw $ level) — not fixed here
    "AUD Employment": "level_change",  # raw pp difference on a rate series — correct as-is, made explicit here
}


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def fetch_latest_actual(series_id, value_type="level_change"):
    """
    value_type controls what "change" means in the returned dict:
      - "level_change": latest - prior, raw units (jobs count, $ level)
      - "percent_change": (latest - prior) / prior * 100, matching a
        predictor's MoM percent range (e.g. CPI's "0.2 to 0.4")
    """
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

    if value_type == "percent_change":
        change = ((latest - prior) / prior) * 100
    else:
        change = latest - prior

    return {"date": obs[0][0], "level": latest, "change": change}


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


def evaluate_inverse_rate_prediction(prediction, actual):
    """
    For indicators like AUD Employment, where the only clean actual
    available is unemployment RATE (not a jobs-count level matching
    the prediction's range units) — checks direction only, not range
    containment, since the units genuinely don't correspond.

    Falling unemployment = stronger labour market = supports "hot".
    Rising unemployment = weaker labour market = supports "soft".
    """
    rate_change = actual["change"]  # positive = unemployment rose

    if prediction["verdict"] == "hot":
        direction_correct = rate_change < 0
    elif prediction["verdict"] == "soft":
        direction_correct = rate_change > 0
    else:
        direction_correct = abs(rate_change) < 0.1  # roughly flat, in percentage points

    return {
        "in_range": None,  # not meaningful here — units don't match
        "direction_correct": direction_correct,
        "predicted_range": prediction["range"],
        "predicted_base_case": prediction["base_case"],
        "actual_value": rate_change,
        "actual_value_note": "unemployment rate change (pp), not a jobs-count — units differ from predicted range",
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
            value_type = VALUE_TYPE.get(prediction["indicator"], "level_change")
            actual = fetch_latest_actual(series_id, value_type=value_type)
        except Exception as e:
            print(f"[performance] failed to fetch actual for {prediction['indicator']}: {e}")
            continue

        if not actual:
            continue

        if prediction["indicator"] in INVERSE_RATE_INDICATORS:
            evaluation = evaluate_inverse_rate_prediction(prediction, actual)
        else:
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

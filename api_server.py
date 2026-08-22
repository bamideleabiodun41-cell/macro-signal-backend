"""
API Server
-----------
Ties nfp_predictor.py, news_pipeline.py, and revision_engine.py together
into a small Flask API the frontend polls every ~10 seconds.

Setup:
    pip install flask requests --break-system-packages
    Set FRED_API_KEY, NEWSAPI_KEY, ANTHROPIC_API_KEY as environment variables
    python api_server.py

Endpoints:
    GET  /api/predictions          -> all active predictions
    GET  /api/predictions/<id>     -> single prediction detail (component breakdown)
    GET  /api/news                 -> breaking news feed, most recent first
    GET  /api/status               -> last poll time, next poll time, health check
    POST /api/refresh               -> manually trigger a poll cycle (for testing)

Background loop: polls news every 90s, checks for revisions after each poll,
and rebuilds each indicator's prediction once daily (or on-demand).
"""

import os
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify

import news_pipeline
import revision_engine
import nfp_predictor
import cpi_predictor
import ppi_predictor
import gdp_predictor
import pce_predictor
import gbp_cpi_predictor
import aud_employment_predictor
import calendar_feed
import scheduler
import performance_tracker

app = Flask(__name__)

STATE = {
    "last_news_poll": None,
    "last_revision_check": None,
    "poll_count": 0,
    "last_calendar_refresh": None,
}

SCHEDULE_FILE = "release_schedule.json"
SCHEDULE_MAX_AGE_HOURS = 12  # re-pull the calendar at most twice a day


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def get_release_schedule(force_refresh=False):
    """
    Returns the cached calendar unless it's stale or force_refresh is set.
    Avoids hammering FRED/ONS on every rebuild-all call.
    """
    cached = load_json(SCHEDULE_FILE, None)
    is_stale = True
    if cached and STATE["last_calendar_refresh"]:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(STATE["last_calendar_refresh"])
        is_stale = age > timedelta(hours=SCHEDULE_MAX_AGE_HOURS)

    if cached and not is_stale and not force_refresh:
        return cached

    schedule = calendar_feed.build_release_schedule()
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(schedule, f, indent=2)
    STATE["last_calendar_refresh"] = datetime.now(timezone.utc).isoformat()
    return schedule


def rescore_ppi():
    """PPI depends on the latest CPI verdict (leading-signal methodology)."""
    latest_cpi = cpi_predictor.build_prediction()
    return ppi_predictor.build_prediction(latest_cpi_verdict=latest_cpi["verdict"])


def rescore_pce():
    """PCE is derived from the live CPI prediction, same chaining pattern as PPI."""
    latest_cpi = cpi_predictor.build_prediction()
    return pce_predictor.build_prediction(latest_cpi_prediction=latest_cpi)


# Map indicator name -> function that returns a fresh prediction dict.
RESCORE_FUNCTIONS = {
    "NFP": nfp_predictor.build_prediction,
    "CPI": cpi_predictor.build_prediction,
    "PPI": rescore_ppi,
    "GDP": gdp_predictor.build_prediction,
    "PCE": rescore_pce,
    "GBP CPI": gbp_cpi_predictor.build_prediction,
    "AUD Employment": aud_employment_predictor.build_prediction,
}


@app.route("/api/predictions", methods=["GET"])
def get_predictions():
    predictions = load_json(revision_engine.PREDICTIONS_FILE, [])
    return jsonify(predictions)


@app.route("/api/predictions/<indicator>", methods=["GET"])
def get_prediction_detail(indicator):
    predictions = load_json(revision_engine.PREDICTIONS_FILE, [])
    match = next((p for p in predictions if p["indicator"].lower() == indicator.lower()), None)
    if not match:
        return jsonify({"error": f"No active prediction for {indicator}"}), 404
    return jsonify(match)


@app.route("/api/news", methods=["GET"])
def get_news():
    feed = load_json(news_pipeline.FEED_FILE, [])
    return jsonify(feed[:50])  # cap payload size


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify({
        "last_news_poll": STATE["last_news_poll"],
        "last_revision_check": STATE["last_revision_check"],
        "poll_count": STATE["poll_count"],
        "server_time": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/refresh", methods=["POST"])
def manual_refresh():
    new_items = run_poll_cycle()
    return jsonify({"new_news_items": new_items})


@app.route("/api/rebuild-all", methods=["POST"])
def rebuild_all_predictions():
    """
    Runs every registered predictor fresh and replaces active_predictions.json.
    Intended to run once daily (or manually) to publish the '24 hours before
    release' predictions — separate from the revision engine, which only
    updates predictions already in that file when breaking news hits.

    Release timestamps now come from calendar_feed.py (live FRED/ONS pull,
    cached for up to 12 hours) instead of being hardcoded.
    """
    release_schedule = get_release_schedule()

    predictions = []
    for indicator, rescore_fn in RESCORE_FUNCTIONS.items():
        try:
            result = rescore_fn()
            result["release_at"] = release_schedule.get(indicator)
            predictions.append(result)
        except Exception as e:
            print(f"[error] failed to build prediction for {indicator}: {e}")

    with open(revision_engine.PREDICTIONS_FILE, "w") as f:
        json.dump(predictions, f, indent=2)

    return jsonify({"rebuilt": [p["indicator"] for p in predictions]})


@app.route("/api/calendar", methods=["GET"])
def get_calendar():
    """Read-only view of the current cached release schedule."""
    return jsonify(get_release_schedule())


@app.route("/api/calendar/refresh", methods=["POST"])
def refresh_calendar():
    """Force a fresh pull from FRED/ONS, bypassing the 12-hour cache."""
    schedule = get_release_schedule(force_refresh=True)
    return jsonify(schedule)


@app.route("/api/performance", methods=["GET"])
def get_performance():
    """Feeds the Prediction Performance page — overall and per-indicator hit rate."""
    return jsonify(performance_tracker.get_accuracy_summary())


def run_poll_cycle():
    """One full cycle: poll news, check revisions, and run the scheduler tick."""
    new_items = news_pipeline.poll_once()
    STATE["last_news_poll"] = datetime.now(timezone.utc).isoformat()
    STATE["poll_count"] += 1

    if new_items > 0 and RESCORE_FUNCTIONS:
        news_feed = load_json(news_pipeline.FEED_FILE, [])
        predictions = load_json(revision_engine.PREDICTIONS_FILE, [])
        revisions = revision_engine.check_for_revisions(news_feed, predictions, RESCORE_FUNCTIONS)
        STATE["last_revision_check"] = datetime.now(timezone.utc).isoformat()
        if revisions:
            print(f"[revision] {len(revisions)} prediction(s) updated")

    # Scheduler tick: publish anything crossing the 24h mark, mark
    # anything crossing its release time as released. Uses the cached
    # calendar so this doesn't hit FRED/ONS every 90 seconds.
    release_schedule = get_release_schedule()
    scheduler.run_scheduler_tick(release_schedule, RESCORE_FUNCTIONS)

    # Check if any newly-released prediction now has an actual value
    # to compare against — feeds the Prediction Performance page.
    try:
        performance_tracker.check_released_predictions()
    except Exception as e:
        print(f"[performance] check failed: {e}")

    return new_items


def background_loop(poll_seconds=90):
    """Runs in a separate thread so Flask can keep serving requests."""
    while True:
        try:
            run_poll_cycle()
        except Exception as e:
            print(f"[error] poll cycle failed: {e}")
        time.sleep(poll_seconds)


# Seed an empty predictions file if none exists yet, so the frontend
# has something to poll on first run.
if not os.path.exists(revision_engine.PREDICTIONS_FILE):
    with open(revision_engine.PREDICTIONS_FILE, "w") as f:
        json.dump([], f)

# Started at module level (not inside __main__) so this runs whether
# the app is launched directly with `python api_server.py` OR imported
# by a WSGI server like gunicorn, which never executes __main__.
poll_thread = threading.Thread(target=background_loop, daemon=True)
poll_thread.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

"""
Revision Engine
----------------
Watches the breaking news feed for items that plausibly affect an
already-published prediction, and triggers a rescore + timestamped
revision note when a match is found.

This is the piece that makes "even minutes before release, flag it"
actually happen — it's the bridge between news_pipeline.py and
nfp_predictor.py (and future cpi_predictor.py, etc).
"""

import json
import os
from datetime import datetime, timezone

import signal_engine

PREDICTIONS_FILE = "active_predictions.json"
REVISION_LOG_FILE = "revision_log.json"

# Maps each indicator to the news categories/keywords that should trigger
# a rescore. Kept simple and explicit rather than fuzzy-matched, so you
# can see exactly why a revision fired.
RELEVANCE_MAP = {
    "NFP": {
        "event_categories": ["war_conflict", "central_bank_surprise", "leadership_change"],
        "keywords": ["jobs", "layoffs", "hiring freeze", "strike", "labor"],
    },
    "CPI": {
        "event_categories": ["energy_supply_shock", "sanctions_trade", "war_conflict"],
        "keywords": ["oil price", "gas price", "supply chain", "tariff"],
    },
    "PPI": {
        "event_categories": ["energy_supply_shock", "sanctions_trade"],
        "keywords": ["commodity", "input costs", "producer"],
    },
    "GDP": {
        "event_categories": ["war_conflict", "central_bank_surprise", "leadership_change"],
        "keywords": ["recession", "growth forecast", "stimulus"],
    },
    "PCE": {
        "event_categories": ["energy_supply_shock", "sanctions_trade"],
        "keywords": ["consumer spending", "retail"],
    },
    "GBP CPI": {
        "event_categories": ["energy_supply_shock", "sanctions_trade", "war_conflict"],
        "keywords": ["boe", "bank of england", "gilt", "uk inflation", "ofgem", "petrol"],
    },
    "AUD Employment": {
        "event_categories": ["war_conflict", "central_bank_surprise", "leadership_change"],
        "keywords": ["rba", "reserve bank of australia", "iron ore", "china demand", "aussie jobs"],
    },
    "FOMC": {
        # Sits at the top of the cascade — reacts to nearly everything
        # the other indicators react to, plus direct Fed commentary.
        "event_categories": ["war_conflict", "central_bank_surprise", "leadership_change",
                              "energy_supply_shock", "sanctions_trade"],
        "keywords": ["fed", "fomc", "powell", "rate decision", "dot plot",
                     "jobs", "inflation", "gdp", "recession"],
    },
}


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def news_item_matches_indicator(news_item, indicator):
    """Check if a news item is relevant enough to an indicator to warrant a rescore."""
    rules = RELEVANCE_MAP.get(indicator)
    if not rules:
        return False

    if news_item.get("event_category") in rules["event_categories"]:
        return True

    text = news_item.get("headline", "").lower()
    if any(kw in text for kw in rules["keywords"]):
        return True

    return False


def check_for_revisions(news_feed, active_predictions, rescore_fn_map):
    """
    For each active prediction, check unprocessed high-impact news items
    for relevance. If a match is found, rescore and log the revision.

    rescore_fn_map: dict mapping indicator name -> callable that returns
    a fresh prediction dict (e.g. {"NFP": nfp_predictor.build_prediction})
    """
    revision_log = load_json(REVISION_LOG_FILE, [])
    already_processed_urls = {entry["news_url"] for entry in revision_log}

    revisions_made = []

    for news_item in news_feed:
        if news_item["impact_score"] < 3:
            continue  # only high-impact news triggers a revision
        if news_item["url"] in already_processed_urls:
            continue

        for prediction in active_predictions:
            indicator = prediction["indicator"]

            # Don't revise a prediction that's already been released —
            # status is the source of truth here, not just the timestamp,
            # since the scheduler is what actually flips this.
            if prediction.get("status") == "released":
                continue

            release_time = datetime.fromisoformat(prediction["release_at"])
            if datetime.now(timezone.utc) > release_time:
                continue

            if not news_item_matches_indicator(news_item, indicator):
                continue

            rescore_fn = rescore_fn_map.get(indicator)
            if not rescore_fn:
                continue

            old_verdict = prediction["verdict"]
            new_prediction = rescore_fn()
            new_prediction = signal_engine.attach_trade_signals(new_prediction)
            new_verdict = new_prediction["verdict"]

            revision_entry = {
                "indicator": indicator,
                "news_url": news_item["url"],
                "news_headline": news_item["headline"],
                "revised_at": datetime.now(timezone.utc).isoformat(),
                "old_verdict": old_verdict,
                "new_verdict": new_verdict,
                "changed": old_verdict != new_verdict,
            }
            revision_log.append(revision_entry)
            revisions_made.append(revision_entry)

            # Update the live prediction in place
            prediction.update(new_prediction)
            prediction["last_revised_at"] = revision_entry["revised_at"]
            prediction["revision_reason"] = news_item["headline"]

    if revisions_made:
        save_json(REVISION_LOG_FILE, revision_log)
        save_json(PREDICTIONS_FILE, active_predictions)

    return revisions_made


if __name__ == "__main__":
    # Manual test run — wire real rescore functions in api_server.py
    news_feed = load_json("breaking_news_feed.json", [])
    predictions = load_json(PREDICTIONS_FILE, [])
    print(f"Loaded {len(news_feed)} news items, {len(predictions)} active predictions")

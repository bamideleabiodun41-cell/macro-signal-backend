"""
NFP Prediction Pipeline
------------------------
Pulls leading indicators from FRED and produces a scorecard-based
directional prediction for the upcoming Nonfarm Payrolls release.

Setup:
    1. Get a free FRED API key: https://fred.stlouisfed.org/docs/api/api_key.html
    2. pip install requests --break-system-packages
    3. Set FRED_API_KEY below or as an environment variable
    4. Run: python nfp_predictor.py

Output: a JSON object matching the dashboard card / detail view schema,
so it can feed directly into the frontend.
"""

import os
import json
import requests
from datetime import datetime, timedelta

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_KEY_HERE")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# FRED series IDs for each leading indicator
SERIES = {
    "adp_employment": "ADPWNUSNERSA",      # ADP National Employment Change
    "initial_claims": "ICSA",               # Initial jobless claims (weekly)
    "continuing_claims": "CCSA",             # Continuing claims
    "jolts_openings": "JTSJOL",              # Job openings
}

# ISM services/manufacturing employment subindex has no free API.
# Enter the latest reading manually each month until a scraper is built.
ISM_SERVICES_EMPLOYMENT_MANUAL = 48.9   # <-- update monthly
ISM_MANUFACTURING_EMPLOYMENT_MANUAL = 47.2  # <-- update monthly


def fetch_series(series_id, limit=6):
    """Pull the most recent N observations for a FRED series."""
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    resp = requests.get(FRED_BASE, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()["observations"]
    # FRED returns "." for missing values — filter those out
    return [(o["date"], float(o["value"])) for o in data if o["value"] != "."]


def score_adp(obs):
    """Compare latest ADP print to trailing 3-month average."""
    latest = obs[0][1]
    trailing_avg = sum(v for _, v in obs[1:4]) / 3
    if latest < trailing_avg * 0.7:
        return "weak", latest
    elif latest > trailing_avg * 1.15:
        return "strong", latest
    return "neutral", latest


def score_claims(obs):
    """4-week average trend direction on initial claims."""
    recent_avg = sum(v for _, v in obs[:4]) / 4
    prior_avg = sum(v for _, v in obs[4:8]) / 4 if len(obs) >= 8 else recent_avg
    if recent_avg > prior_avg * 1.05:
        return "weak", recent_avg   # rising claims = weak labor market
    elif recent_avg < prior_avg * 0.95:
        return "strong", recent_avg
    return "neutral", recent_avg


def score_jolts(obs):
    latest = obs[0][1]
    prior = obs[1][1]
    change_pct = (latest - prior) / prior
    if change_pct < -0.03:
        return "weak", latest
    elif change_pct > 0.03:
        return "strong", latest
    return "neutral", latest


def score_ism(value):
    if value < 48:
        return "weak", value
    elif value > 52:
        return "strong", value
    return "neutral", value


def build_prediction():
    adp_obs = fetch_series(SERIES["adp_employment"])
    claims_obs = fetch_series(SERIES["initial_claims"], limit=10)
    jolts_obs = fetch_series(SERIES["jolts_openings"])

    adp_signal, adp_val = score_adp(adp_obs)
    claims_signal, claims_val = score_claims(claims_obs)
    jolts_signal, jolts_val = score_jolts(jolts_obs)
    ism_signal, ism_val = score_ism(ISM_SERVICES_EMPLOYMENT_MANUAL)

    signals = [adp_signal, claims_signal, jolts_signal, ism_signal]
    weak_count = signals.count("weak")
    strong_count = signals.count("strong")

    # Simple scorecard verdict — tune weights as you validate against
    # actual releases over the coming months.
    if weak_count >= 3:
        verdict = "soft"
        base_case, low, high = 75_000, 40_000, 105_000
    elif strong_count >= 3:
        verdict = "hot"
        base_case, low, high = 165_000, 130_000, 200_000
    else:
        verdict = "in-line"
        base_case, low, high = 110_000, 85_000, 135_000

    confidence = "high" if abs(weak_count - strong_count) >= 3 else "medium"

    result = {
        "indicator": "NFP",
        "generated_at": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "base_case": base_case,
        "range": [low, high],
        "confidence": confidence,
        "components": [
            {"name": "ADP employment", "signal": adp_signal, "value": adp_val},
            {"name": "Initial claims (4wk avg)", "signal": claims_signal, "value": round(claims_val)},
            {"name": "JOLTS openings", "signal": jolts_signal, "value": jolts_val},
            {"name": "ISM services employment", "signal": ism_signal, "value": ism_val},
        ],
    }
    return result


if __name__ == "__main__":
    prediction = build_prediction()
    print(json.dumps(prediction, indent=2))

    # Write to a file the frontend/dashboard can read
    with open("nfp_prediction_latest.json", "w") as f:
        json.dump(prediction, f, indent=2)

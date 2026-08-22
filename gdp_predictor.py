"""
GDP Prediction Pipeline (USD)
--------------------------------
GDP is quarterly and lower-frequency than NFP/CPI, so the "leading
indicator" set is different: retail sales, industrial production, and
the Atlanta Fed's own GDPNow model (public, free, and genuinely good —
used here as a benchmark input rather than reinventing it).

Setup: needs FRED_API_KEY.
Run: python gdp_predictor.py
"""

import os
import json
import requests
from datetime import datetime

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_KEY_HERE")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "retail_sales": "RSAFS",           # Retail sales, advance
    "industrial_production": "INDPRO",
    "gdpnow": "GDPNOW",                # Atlanta Fed GDPNow nowcast — direct FRED series
}


def fetch_series(series_id, limit=6):
    params = {
        "series_id": series_id, "api_key": FRED_API_KEY,
        "file_type": "json", "sort_order": "desc", "limit": limit,
    }
    resp = requests.get(FRED_BASE, params=params, timeout=10)
    resp.raise_for_status()
    return [(o["date"], float(o["value"])) for o in resp.json()["observations"] if o["value"] != "."]


def score_retail_sales(obs):
    latest, prior = obs[0][1], obs[1][1]
    change_pct = (latest - prior) / prior
    if change_pct > 0.005:
        return "strong", change_pct
    elif change_pct < -0.002:
        return "weak", change_pct
    return "neutral", change_pct


def score_industrial_production(obs):
    latest, prior = obs[0][1], obs[1][1]
    change_pct = (latest - prior) / prior
    if change_pct > 0.003:
        return "strong", change_pct
    elif change_pct < -0.002:
        return "weak", change_pct
    return "neutral", change_pct


def score_gdpnow(obs):
    """
    GDPNow already IS a GDP estimate, not a component — treat it as the
    anchor rather than one vote among equals. Still tagged with a
    weak/neutral/strong label for consistent scorecard display.
    """
    latest = obs[0][1]
    if latest > 2.5:
        return "strong", latest
    elif latest < 1.0:
        return "weak", latest
    return "neutral", latest


def build_prediction():
    retail_obs = fetch_series(SERIES["retail_sales"])
    indpro_obs = fetch_series(SERIES["industrial_production"])
    gdpnow_obs = fetch_series(SERIES["gdpnow"])

    retail_signal, retail_val = score_retail_sales(retail_obs)
    indpro_signal, indpro_val = score_industrial_production(indpro_obs)
    gdpnow_signal, gdpnow_val = score_gdpnow(gdpnow_obs)

    # GDPNow is weighted as the anchor — it already synthesizes far more
    # inputs than we're tracking here, so it gets 2 votes to the others' 1.
    weighted_signals = [retail_signal, indpro_signal, gdpnow_signal, gdpnow_signal]
    strong_count = weighted_signals.count("strong")
    weak_count = weighted_signals.count("weak")

    if strong_count >= 2:
        verdict, base_case, low, high = "hot", 3.0, 2.4, 3.6
    elif weak_count >= 2:
        verdict, base_case, low, high = "soft", 0.8, 0.2, 1.4
    else:
        verdict = "in-line"
        base_case, low, high = gdpnow_val, gdpnow_val - 0.4, gdpnow_val + 0.4

    return {
        "indicator": "GDP",
        "generated_at": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "base_case": round(base_case, 2),  # annualized QoQ %
        "range": [round(low, 2), round(high, 2)],
        "confidence": "high" if abs(strong_count - weak_count) >= 2 else "medium",
        "components": [
            {"name": "Retail sales trend", "signal": retail_signal, "value": round(retail_val, 4)},
            {"name": "Industrial production trend", "signal": indpro_signal, "value": round(indpro_val, 4)},
            {"name": "Atlanta Fed GDPNow (anchor)", "signal": gdpnow_signal, "value": gdpnow_val},
        ],
    }


if __name__ == "__main__":
    prediction = build_prediction()
    print(json.dumps(prediction, indent=2))
    with open("gdp_prediction_latest.json", "w") as f:
        json.dump(prediction, f, indent=2)

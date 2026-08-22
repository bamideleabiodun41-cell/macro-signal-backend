"""
GBP CPI Prediction Pipeline
------------------------------
Uses the UK Office for National Statistics (ONS) API — free, no key
required. Structure mirrors cpi_predictor.py (USD) but swapped to
UK-relevant inputs: UK petrol prices, Ofgem energy price cap moves,
and ONS's own CPI series for trend context.

ONS API docs: https://developer.ons.gov.uk/

Note: ONS API structure differs from FRED (dataset/edition/version
paths rather than a flat series lookup) — the fetch function below
targets their standard CPI dataset endpoint. Verify the exact series
code (cpih01 vs cpi mm23 etc.) against ONS's current API browser
before relying on this in production, as their dataset IDs shift
periodically.
"""

import os
import json
import requests
from datetime import datetime

ONS_BASE = "https://api.ons.gov.uk/timeseries"

# ONS CPI series codes (verify current codes at api.ons.gov.uk before use)
CPI_SERIES_ID = "D7G7"        # CPI annual rate, all items
CPI_CORE_SERIES_ID = "D7G8"   # CPI core (example — confirm against ONS browser)

# UK petrol prices have no single free real-time API as clean as FRED's;
# RAC Foundation and gov.uk publish weekly fuel price data. Manual entry
# recommended until a scraper is built for these.
UK_PETROL_PRICE_TREND_MANUAL = 0.01   # <-- update weekly, % change vs prior month
OFGEM_PRICE_CAP_DIRECTION_MANUAL = "flat"  # "up", "down", or "flat" — updates quarterly


def fetch_ons_series(series_id, dataset="mm23"):
    """
    ONS timeseries API returns nested JSON. This pulls the most recent
    months' data points from the standard structure.
    """
    url = f"{ONS_BASE}/{series_id}/dataset/{dataset}/data"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    months = data.get("months", [])
    # Most recent entries last in ONS's response — reverse for consistency
    return [(m["date"], float(m["value"])) for m in months[-6:]][::-1]


def score_petrol(manual_trend):
    if manual_trend > 0.03:
        return "strong", manual_trend
    elif manual_trend < -0.03:
        return "weak", manual_trend
    return "neutral", manual_trend


def score_energy_cap(direction):
    mapping = {"up": ("strong", 1), "down": ("weak", -1), "flat": ("neutral", 0)}
    return mapping.get(direction, ("neutral", 0))


def score_cpi_trend(obs):
    if len(obs) < 2:
        return "neutral", 0
    latest, prior = obs[0][1], obs[1][1]
    change = latest - prior
    if change > 0.2:
        return "strong", change
    elif change < -0.2:
        return "weak", change
    return "neutral", change


def build_prediction():
    try:
        cpi_obs = fetch_ons_series(CPI_SERIES_ID)
        cpi_signal, cpi_val = score_cpi_trend(cpi_obs)
    except Exception as e:
        print(f"[warning] ONS fetch failed ({e}), falling back to neutral for CPI trend component")
        cpi_signal, cpi_val = "neutral", 0

    petrol_signal, petrol_val = score_petrol(UK_PETROL_PRICE_TREND_MANUAL)
    energy_cap_signal, energy_cap_val = score_energy_cap(OFGEM_PRICE_CAP_DIRECTION_MANUAL)

    signals = [cpi_signal, petrol_signal, energy_cap_signal]
    strong_count = signals.count("strong")
    weak_count = signals.count("weak")

    if strong_count >= 2:
        verdict, base_case, low, high = "hot", 0.4, 0.3, 0.5
    elif weak_count >= 2:
        verdict, base_case, low, high = "soft", 0.1, 0.0, 0.2
    else:
        verdict, base_case, low, high = "in-line", 0.25, 0.15, 0.35

    return {
        "indicator": "GBP CPI",
        "generated_at": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "base_case": base_case,
        "range": [low, high],
        "confidence": "high" if abs(strong_count - weak_count) >= 2 else "medium",
        "components": [
            {"name": "ONS CPI trend", "signal": cpi_signal, "value": cpi_val},
            {"name": "UK petrol price trend (manual)", "signal": petrol_signal, "value": petrol_val},
            {"name": "Ofgem energy price cap direction (manual)", "signal": energy_cap_signal, "value": energy_cap_val},
        ],
    }


if __name__ == "__main__":
    prediction = build_prediction()
    print(json.dumps(prediction, indent=2))
    with open("gbp_cpi_prediction_latest.json", "w") as f:
        json.dump(prediction, f, indent=2)

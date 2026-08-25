"""
PPI Prediction Pipeline (USD)
--------------------------------
Per your methodology after the prior PPI miss: use the most recent CPI
print as a leading signal for PPI direction, plus real-time commodity/
input cost tracking rather than anchoring to the prior month's PPI pattern.

Setup: same as cpi_predictor.py — needs FRED_API_KEY.
Run: python ppi_predictor.py
"""

import os
import json
import requests
from datetime import datetime

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_KEY_HERE")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "ppi_final_demand": "PPIFIS",         # PPI Final Demand
    "ppi_core": "WPSFD4131",              # PPI less food and energy
    "intermediate_input_costs": "WPSID61",  # PPI: Processed Goods for Intermediate Demand
    "wti_crude": "DCOILWTICO",
}


def fetch_series(series_id, limit=6):
    params = {
        "series_id": series_id, "api_key": FRED_API_KEY,
        "file_type": "json", "sort_order": "desc", "limit": limit,
    }
    resp = requests.get(FRED_BASE, params=params, timeout=10)
    resp.raise_for_status()
    return [(o["date"], float(o["value"])) for o in resp.json()["observations"] if o["value"] != "."]


def score_cpi_leading_signal(latest_cpi_verdict):
    """
    Core of your methodology: CPI direction leads PPI direction.
    Pass in the verdict string from cpi_predictor.py's most recent output.
    """
    mapping = {"hot": ("strong", 1), "in-line": ("neutral", 0), "soft": ("weak", -1)}
    return mapping.get(latest_cpi_verdict, ("neutral", 0))


def score_intermediate_input_costs(obs):
    """
    WPSID61 is a price index level (not a 0-100 diffusion index like ISM
    was), so we score it by month-over-month trend rather than a fixed
    threshold — rising intermediate input costs typically feed through
    to final-demand PPI within 1-2 months.
    """
    latest = obs[0][1]
    prior = obs[1][1]
    change_pct = (latest - prior) / prior
    if change_pct > 0.01:
        return "strong", change_pct
    elif change_pct < -0.005:
        return "weak", change_pct
    return "neutral", change_pct


def score_commodity_trend(wti_obs):
    latest = wti_obs[0][1]
    prior = wti_obs[4][1] if len(wti_obs) > 4 else wti_obs[-1][1]
    change_pct = (latest - prior) / prior
    if change_pct > 0.04:
        return "strong", change_pct
    elif change_pct < -0.04:
        return "weak", change_pct
    return "neutral", change_pct


def build_prediction(latest_cpi_verdict="in-line"):
    """
    latest_cpi_verdict: pass the verdict from the most recent cpi_predictor.py
    run — this is the leading-signal linkage your methodology calls for.
    """
    input_cost_obs = fetch_series(SERIES["intermediate_input_costs"])
    wti_obs = fetch_series(SERIES["wti_crude"])

    cpi_signal, cpi_val = score_cpi_leading_signal(latest_cpi_verdict)
    input_cost_signal, input_cost_val = score_intermediate_input_costs(input_cost_obs)
    commodity_signal, commodity_val = score_commodity_trend(wti_obs)

    signals = [cpi_signal, input_cost_signal, commodity_signal]
    strong_count = signals.count("strong")
    weak_count = signals.count("weak")

    if strong_count >= 2:
        verdict, base_case, low, high = "hot", 0.3, 0.2, 0.4
    elif weak_count >= 2:
        verdict, base_case, low, high = "soft", 0.0, -0.1, 0.1
    else:
        verdict, base_case, low, high = "in-line", 0.15, 0.05, 0.25

    return {
        "indicator": "PPI",
        "generated_at": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "base_case": base_case,
        "range": [low, high],
        "confidence": "high" if abs(strong_count - weak_count) >= 2 else "medium",
        "components": [
            {"name": f"Prior CPI signal ({latest_cpi_verdict})", "signal": cpi_signal, "value": cpi_val},
            {"name": "Intermediate input costs (WPSID61)", "signal": input_cost_signal, "value": round(input_cost_val, 4)},
            {"name": "WTI crude trend", "signal": commodity_signal, "value": round(commodity_val, 4)},
        ],
    }


if __name__ == "__main__":
    # In production, api_server.py would pass in the real latest CPI verdict
    # from cpi_predictor.py's last output rather than a hardcoded default.
    prediction = build_prediction(latest_cpi_verdict="in-line")
    print(json.dumps(prediction, indent=2))
    with open("ppi_prediction_latest.json", "w") as f:
        json.dump(prediction, f, indent=2)

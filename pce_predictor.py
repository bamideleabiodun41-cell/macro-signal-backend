"""
PCE Prediction Pipeline (USD)
--------------------------------
PCE (Fed's preferred inflation gauge) tracks CPI closely but with
different category weights — healthcare weighs more heavily in PCE
than CPI, housing/shelter weighs less. Rather than duplicate a full
independent model, this predictor takes the CPI prediction as its base
and applies a weighting adjustment, which is both realistic (PCE
genuinely correlates with and lags CPI) and keeps the two predictors
properly linked the way your CPI-leads-PPI logic already works.

Setup: needs FRED_API_KEY (used for the healthcare cost series).
Run: python pce_predictor.py
"""

import os
import json
import requests
from datetime import datetime

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_KEY_HERE")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "healthcare_cpi": "CUSR0000SAM",  # Medical care CPI, as a PCE healthcare-weight proxy
}


def fetch_series(series_id, limit=4):
    params = {
        "series_id": series_id, "api_key": FRED_API_KEY,
        "file_type": "json", "sort_order": "desc", "limit": limit,
    }
    resp = requests.get(FRED_BASE, params=params, timeout=10)
    resp.raise_for_status()
    return [(o["date"], float(o["value"])) for o in resp.json()["observations"] if o["value"] != "."]


def score_healthcare_trend(obs):
    latest, prior = obs[0][1], obs[1][1]
    change_pct = (latest - prior) / prior
    if change_pct > 0.004:
        return "strong", change_pct
    elif change_pct < 0.001:
        return "weak", change_pct
    return "neutral", change_pct


def build_prediction(latest_cpi_prediction=None):
    """
    latest_cpi_prediction: pass the full dict returned by
    cpi_predictor.build_prediction() — this is the CPI-leads-PCE
    linkage, same pattern as PPI reading off CPI's verdict.
    """
    if latest_cpi_prediction is None:
        # Fallback if called standalone without the CPI chain wired in
        latest_cpi_prediction = {"verdict": "in-line", "base_case": 0.25}

    healthcare_obs = fetch_series(SERIES["healthcare_cpi"])
    healthcare_signal, healthcare_val = score_healthcare_trend(healthcare_obs)

    cpi_verdict = latest_cpi_prediction["verdict"]
    cpi_base = latest_cpi_prediction["base_case"]

    # PCE typically runs a touch below CPI headline due to substitution
    # effects and different weighting — apply a modest haircut rather
    # than assuming a 1:1 read-through.
    pce_base_case = round(cpi_base * 0.85, 2)

    if healthcare_signal == "strong" and cpi_verdict == "hot":
        verdict = "hot"
    elif healthcare_signal == "weak" and cpi_verdict == "soft":
        verdict = "soft"
    else:
        verdict = cpi_verdict  # default to following CPI's lead

    low, high = round(pce_base_case - 0.1, 2), round(pce_base_case + 0.1, 2)

    return {
        "indicator": "PCE",
        "generated_at": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "base_case": pce_base_case,
        "range": [low, high],
        "confidence": "medium",  # derived-from-CPI predictions default to medium, not high
        "components": [
            {"name": f"CPI read-through ({cpi_verdict})", "signal": cpi_verdict if cpi_verdict != "in-line" else "neutral", "value": cpi_base},
            {"name": "Healthcare cost trend (PCE-weighted)", "signal": healthcare_signal, "value": round(healthcare_val, 4)},
        ],
    }


if __name__ == "__main__":
    prediction = build_prediction()
    print(json.dumps(prediction, indent=2))
    with open("pce_prediction_latest.json", "w") as f:
        json.dump(prediction, f, indent=2)

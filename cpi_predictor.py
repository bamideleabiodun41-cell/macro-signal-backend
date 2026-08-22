"""
CPI Prediction Pipeline
-------------------------
Same pattern as nfp_predictor.py, adapted to your CPI methodology:
component breakdown (energy, food, goods, services) rather than one
blended number, real-time energy tracking instead of anchoring to the
prior month's pattern, and a range/base case rather than a point estimate.

Setup:
    1. Free FRED API key: https://fred.stlouisfed.org/docs/api/api_key.html
    2. pip install requests --break-system-packages
    3. Set FRED_API_KEY below or as an environment variable
    4. Run: python cpi_predictor.py

Output: JSON matching the same prediction schema as nfp_predictor.py,
so it plugs into api_server.py / RESCORE_FUNCTIONS the same way.
"""

import os
import json
import requests
from datetime import datetime

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_KEY_HERE")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "gasoline_weekly": "GASREGW",       # US Regular Gasoline Price, weekly
    "wti_crude": "DCOILWTICO",           # WTI crude oil, daily
    "cpi_headline": "CPIAUCSL",          # prior CPI prints, for trend context
    "cpi_core": "CPILFESL",              # core CPI, for trend context
    "shelter_cpi": "CUSR0000SAH1",       # shelter component
}

# Manheim used vehicle index has no free API — update manually each month.
MANHEIM_INDEX_MOM_CHANGE_MANUAL = -0.4  # <-- update monthly, % change


def fetch_series(series_id, limit=8):
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
    return [(o["date"], float(o["value"])) for o in data if o["value"] != "."]


def score_energy(gasoline_obs, wti_obs):
    """
    Per your methodology: track real-time energy trend rather than
    anchoring to the prior month's pattern. Compares latest weekly
    gasoline price to the average of the reference month.
    """
    latest_gas = gasoline_obs[0][1]
    month_avg_gas = sum(v for _, v in gasoline_obs[:4]) / min(4, len(gasoline_obs))
    gas_change_pct = (latest_gas - month_avg_gas) / month_avg_gas

    latest_wti = wti_obs[0][1]
    prior_wti = wti_obs[4][1] if len(wti_obs) > 4 else wti_obs[-1][1]
    wti_change_pct = (latest_wti - prior_wti) / prior_wti

    combined = (gas_change_pct + wti_change_pct) / 2
    if combined > 0.03:
        return "strong", combined  # energy pushing CPI up
    elif combined < -0.03:
        return "weak", combined    # energy pulling CPI down
    return "neutral", combined


def score_shelter(shelter_obs):
    """Shelter is sticky — look at the trend over the last few prints, not just latest."""
    latest = shelter_obs[0][1]
    prior = shelter_obs[1][1]
    mom_change = (latest - prior) / prior
    if mom_change > 0.004:
        return "strong", mom_change
    elif mom_change < 0.002:
        return "weak", mom_change
    return "neutral", mom_change


def score_used_cars(manheim_change):
    if manheim_change > 0.5:
        return "strong", manheim_change
    elif manheim_change < -0.5:
        return "weak", manheim_change
    return "neutral", manheim_change


def score_core_trend(core_obs):
    """Use the two most recent core CPI MoM changes as a momentum signal."""
    if len(core_obs) < 2:
        return "neutral", 0
    latest = core_obs[0][1]
    prior = core_obs[1][1]
    mom_change = (latest - prior) / prior
    if mom_change > 0.003:
        return "strong", mom_change
    elif mom_change < 0.001:
        return "weak", mom_change
    return "neutral", mom_change


def build_prediction():
    gasoline_obs = fetch_series(SERIES["gasoline_weekly"])
    wti_obs = fetch_series(SERIES["wti_crude"])
    shelter_obs = fetch_series(SERIES["shelter_cpi"])
    core_obs = fetch_series(SERIES["cpi_core"])

    energy_signal, energy_val = score_energy(gasoline_obs, wti_obs)
    shelter_signal, shelter_val = score_shelter(shelter_obs)
    used_car_signal, used_car_val = score_used_cars(MANHEIM_INDEX_MOM_CHANGE_MANUAL)
    core_signal, core_val = score_core_trend(core_obs)

    signals = [energy_signal, shelter_signal, used_car_signal, core_signal]
    strong_count = signals.count("strong")
    weak_count = signals.count("weak")

    # Weighted toward energy + shelter since those historically drive the
    # bulk of month-to-month CPI surprise, per your prior forecasting notes.
    if strong_count >= 3 or (energy_signal == "strong" and shelter_signal == "strong"):
        verdict = "hot"
        base_case, low, high = 0.4, 0.3, 0.5
    elif weak_count >= 3 or (energy_signal == "weak" and shelter_signal == "weak"):
        verdict = "soft"
        base_case, low, high = 0.1, 0.0, 0.2
    else:
        verdict = "in-line"
        base_case, low, high = 0.3, 0.2, 0.4

    confidence = "high" if abs(strong_count - weak_count) >= 3 else "medium"

    result = {
        "indicator": "CPI",
        "generated_at": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "base_case": base_case,  # headline MoM %
        "range": [low, high],
        "confidence": confidence,
        "components": [
            {"name": "Energy (gas + WTI real-time trend)", "signal": energy_signal, "value": round(energy_val, 4)},
            {"name": "Shelter (MoM trend)", "signal": shelter_signal, "value": round(shelter_val, 4)},
            {"name": "Used vehicles (Manheim, manual)", "signal": used_car_signal, "value": used_car_val},
            {"name": "Core CPI momentum", "signal": core_signal, "value": round(core_val, 4)},
        ],
    }
    return result


if __name__ == "__main__":
    prediction = build_prediction()
    print(json.dumps(prediction, indent=2))

    with open("cpi_prediction_latest.json", "w") as f:
        json.dump(prediction, f, indent=2)

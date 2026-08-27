"""
FOMC Rate-Decision Prediction Pipeline
------------------------------------------
This sits at the TOP of the prediction cascade — the actual decision
that CPI, NFP, PCE, and GDP exist to inform. Rather than building yet
another independent model, this predictor explicitly chains off the
other four's LIVE verdicts (same pattern as PPI/PCE chaining off CPI),
because that's genuinely how Fed-watchers reason: strong labor data +
sticky inflation = hawkish lean; cooling data on both = dovish lean.

Adds two direct market-based signals on top of the chained verdicts:
    - Current Fed funds rate level (context, not a signal by itself)
    - 2-year Treasury yield trend (a real market proxy for near-term
      rate expectations — 2yr yields move ahead of Fed decisions as
      traders price in what they expect)

Setup: needs FRED_API_KEY. Also needs the other four predictor modules
importable (nfp_predictor, cpi_predictor, pce_predictor, gdp_predictor).
Run: python fomc_predictor.py
"""

import os
import json
import requests
from datetime import datetime

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_KEY_HERE")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "fed_funds_rate": "FEDFUNDS",   # Effective Federal Funds Rate
    "treasury_2yr": "DGS2",          # 2-Year Treasury yield — market rate-expectation proxy
}

# Official 2026 FOMC meeting dates, confirmed directly from the Federal
# Reserve's own published calendar (federalreserve.gov) — the Fed
# announces its full-year schedule in advance, so unlike other
# indicators these don't need to be estimated.
FOMC_2026_MEETING_DATES = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]

# Meetings with a Summary of Economic Projections ("dot plot") carry
# more weight — the market treats these as more consequential than a
# statement-only meeting.
SEP_MEETING_DATES = {"2026-03-18", "2026-06-17", "2026-09-16", "2026-12-09"}


def fetch_series(series_id, limit=6):
    params = {
        "series_id": series_id, "api_key": FRED_API_KEY,
        "file_type": "json", "sort_order": "desc", "limit": limit,
    }
    resp = requests.get(FRED_BASE, params=params, timeout=10)
    resp.raise_for_status()
    return [(o["date"], float(o["value"])) for o in resp.json()["observations"] if o["value"] != "."]


def score_2yr_yield_trend(obs):
    """
    2-year yield trend as a market-expectation proxy. Falling 2yr
    yields typically signal the market is pricing in cuts; rising
    yields signal the market expects hikes or a longer hold.
    """
    latest = obs[0][1]
    month_ago = obs[-1][1] if len(obs) >= 6 else obs[-1][1]
    change = latest - month_ago
    if change < -0.15:
        return "dovish", change
    elif change > 0.15:
        return "hawkish", change
    return "neutral", change


def chain_indicator_verdict(indicator_name, verdict):
    """
    Maps each chained indicator's verdict to a hawkish/dovish/neutral
    lean for the Fed decision. Hot data (strong growth/inflation/jobs)
    supports a hawkish lean (hold or hike); soft data supports dovish
    (cut). This directly implements the cascade: CPI/NFP/PCE/GDP feed
    the FOMC read, the same way real market participants reason.
    """
    if verdict == "hot":
        return "hawkish"
    elif verdict == "soft":
        return "dovish"
    return "neutral"


def build_prediction():
    """
    Chains off the other four predictors' LIVE output rather than
    re-deriving inflation/growth signals independently — this is the
    deliberate cascade structure: FOMC sits downstream of CPI, NFP,
    PCE, and GDP, exactly as real Fed-watchers reason about it.
    """
    # Import here (not at module top) to avoid a circular-import risk
    # if these modules ever import fomc_predictor back for anything.
    import cpi_predictor
    import nfp_predictor
    import pce_predictor
    import gdp_predictor

    cpi_result = cpi_predictor.build_prediction()
    nfp_result = nfp_predictor.build_prediction()
    gdp_result = gdp_predictor.build_prediction()
    pce_result = pce_predictor.build_prediction(latest_cpi_prediction=cpi_result)

    cpi_lean = chain_indicator_verdict("CPI", cpi_result["verdict"])
    nfp_lean = chain_indicator_verdict("NFP", nfp_result["verdict"])
    gdp_lean = chain_indicator_verdict("GDP", gdp_result["verdict"])
    pce_lean = chain_indicator_verdict("PCE", pce_result["verdict"])

    # Market-based signal, independent of the chained data predictors
    yield_obs = fetch_series(SERIES["treasury_2yr"])
    yield_lean, yield_change = score_2yr_yield_trend(yield_obs)

    fed_funds_obs = fetch_series(SERIES["fed_funds_rate"], limit=1)
    current_rate = fed_funds_obs[0][1] if fed_funds_obs else None

    leans = [cpi_lean, nfp_lean, gdp_lean, pce_lean, yield_lean]
    hawkish_count = leans.count("hawkish")
    dovish_count = leans.count("dovish")

    # PCE is the Fed's explicitly stated preferred inflation gauge —
    # weight it more heavily than a simple majority vote would.
    if pce_lean == "hawkish":
        hawkish_count += 1
    elif pce_lean == "dovish":
        dovish_count += 1

    if hawkish_count >= dovish_count + 2:
        verdict = "hawkish hold"
        simple_verdict = "Hold likely — hawkish tone"
    elif dovish_count >= hawkish_count + 2:
        verdict = "cut likely"
        simple_verdict = "Rate cut likely"
    elif dovish_count > hawkish_count:
        verdict = "dovish hold"
        simple_verdict = "Hold likely — dovish tone, cut signaling possible"
    else:
        verdict = "hold"
        simple_verdict = "Hold likely — mixed signals"

    confidence = "high" if abs(hawkish_count - dovish_count) >= 3 else "medium"

    result = {
        "indicator": "FOMC",
        "generated_at": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "simple_verdict_override": simple_verdict,  # more descriptive than the standard hot/soft/in-line labels
        "base_case": current_rate,  # current effective rate, for context — not a "prediction number" like other indicators
        "range": [current_rate, current_rate] if current_rate else [None, None],
        "confidence": confidence,
        "components": [
            {"name": f"CPI (chained, verdict={cpi_result['verdict']})", "signal": cpi_lean, "value": cpi_result["base_case"]},
            {"name": f"NFP (chained, verdict={nfp_result['verdict']})", "signal": nfp_lean, "value": nfp_result["base_case"]},
            {"name": f"PCE (chained, Fed's preferred gauge, verdict={pce_result['verdict']})", "signal": pce_lean, "value": pce_result["base_case"]},
            {"name": f"GDP (chained, verdict={gdp_result['verdict']})", "signal": gdp_lean, "value": gdp_result["base_case"]},
            {"name": "2yr Treasury yield trend (market expectation)", "signal": yield_lean, "value": round(yield_change, 3)},
        ],
        "current_fed_funds_rate": current_rate,
    }
    return result


if __name__ == "__main__":
    prediction = build_prediction()
    print(json.dumps(prediction, indent=2))
    with open("fomc_prediction_latest.json", "w") as f:
        json.dump(prediction, f, indent=2)

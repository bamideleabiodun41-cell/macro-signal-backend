"""
AUD Employment / CPI Prediction Pipeline
--------------------------------------------
Australia's data landscape doesn't have a FRED-equivalent free API with
broad coverage. Two realistic paths, both handled below:

1. RBA (Reserve Bank of Australia) publishes statistical tables as
   downloadable CSVs — no API, but stable URLs that can be fetched
   and parsed directly. Used here for interest-rate-relevant series.
2. ABS (Australian Bureau of Statistics) has a SDMX-format API but it's
   inconsistent and dataset IDs shift. For labour force data (the
   indicator you've been tracking — employment change, unemployment
   rate), manual entry from the monthly ABS release is the reliable
   starting point until a dedicated ABS SDMX client is worth building.

This mirrors the same scorecard structure as the other predictors so
it plugs into api_server.py the same way.
"""

import os
import json
import requests
import csv
import io
from datetime import datetime

RBA_CASH_RATE_CSV_URL = "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv"

# --- Manual entry block ---
# Update these from the ABS monthly Labour Force release
# (https://www.abs.gov.au/statistics/labour/employment-and-unemployment)
# until an automated ABS pull is built.
ABS_EMPLOYMENT_CHANGE_MANUAL = 25_000     # latest monthly employment change
ABS_EMPLOYMENT_CHANGE_PRIOR_MANUAL = 15_000  # prior month, for trend
ABS_UNEMPLOYMENT_RATE_MANUAL = 4.2        # latest unemployment rate, %
ABS_UNEMPLOYMENT_RATE_PRIOR_MANUAL = 4.1  # prior month
# ---------------------------


def fetch_rba_cash_rate_trend():
    """
    RBA publishes cash rate history as CSV. Used here as a proxy for
    monetary policy stance, which colors the dovish/hawkish framing
    around Australian labour data releases.
    """
    resp = requests.get(RBA_CASH_RATE_CSV_URL, timeout=10)
    resp.raise_for_status()
    reader = csv.reader(io.StringIO(resp.text))
    rows = [row for row in reader if row and row[0].strip()]
    # RBA CSVs have a metadata header block before data rows start —
    # in production, locate the header row by column name rather than
    # a fixed offset, since RBA occasionally changes the preamble length.
    return rows[-5:]  # most recent rows, raw — parse further as needed


def score_employment_change():
    latest = ABS_EMPLOYMENT_CHANGE_MANUAL
    prior = ABS_EMPLOYMENT_CHANGE_PRIOR_MANUAL
    if latest > prior * 1.2 and latest > 20_000:
        return "strong", latest
    elif latest < prior * 0.7 or latest < 0:
        return "weak", latest
    return "neutral", latest


def score_unemployment_rate():
    latest = ABS_UNEMPLOYMENT_RATE_MANUAL
    prior = ABS_UNEMPLOYMENT_RATE_PRIOR_MANUAL
    change = latest - prior
    # Rising unemployment = weak labour market = dovish signal
    if change > 0.1:
        return "weak", change
    elif change < -0.1:
        return "strong", change
    return "neutral", change


def score_rba_stance(cash_rate_rows):
    """
    Placeholder scoring — once fetch_rba_cash_rate_trend() is parsing
    actual numeric values, compare latest cash rate to prior decision
    to detect a hold/cut/hike stance. Currently returns neutral since
    the raw CSV rows need column-mapping specific to RBA's current
    table format.
    """
    return "neutral", None


def build_prediction():
    employment_signal, employment_val = score_employment_change()
    unemployment_signal, unemployment_val = score_unemployment_rate()

    try:
        rba_rows = fetch_rba_cash_rate_trend()
        rba_signal, rba_val = score_rba_stance(rba_rows)
    except Exception as e:
        print(f"[warning] RBA fetch failed ({e}), defaulting stance signal to neutral")
        rba_signal, rba_val = "neutral", None

    signals = [employment_signal, unemployment_signal, rba_signal]
    strong_count = signals.count("strong")
    weak_count = signals.count("weak")

    if strong_count >= 2:
        verdict, base_case, low, high = "hot", 35_000, 20_000, 50_000
    elif weak_count >= 2:
        verdict, base_case, low, high = "soft", 5_000, -10_000, 15_000
    else:
        verdict, base_case, low, high = "in-line", 20_000, 10_000, 30_000

    return {
        "indicator": "AUD Employment",
        "generated_at": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "base_case": base_case,
        "range": [low, high],
        "confidence": "high" if abs(strong_count - weak_count) >= 2 else "medium",
        "components": [
            {"name": "Employment change (ABS, manual)", "signal": employment_signal, "value": employment_val},
            {"name": "Unemployment rate trend (ABS, manual)", "signal": unemployment_signal, "value": round(unemployment_val, 2)},
            {"name": "RBA policy stance", "signal": rba_signal, "value": rba_val},
        ],
    }


if __name__ == "__main__":
    prediction = build_prediction()
    print(json.dumps(prediction, indent=2))
    with open("aud_employment_prediction_latest.json", "w") as f:
        json.dump(prediction, f, indent=2)

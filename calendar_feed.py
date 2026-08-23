"""
Calendar Feed
--------------
Pulls upcoming release dates from official statistical agencies rather
than scraping a retail calendar site (ForexFactory/Investing.com-style
calendars sit in a legal gray area on scraping ToS — this sticks to
each country's own statistics office, which is free and unambiguous).

Sources:
    - US (NFP, CPI, PPI, GDP, PCE): FRED's release-dates API
    - UK (GBP CPI): ONS release calendar API
    - Australia (AUD Employment): ABS doesn't have a clean release-dates
      API — falls back to manual entry, same pattern as its data feed

Setup: needs FRED_API_KEY (same one used elsewhere).
Run: python calendar_feed.py
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_KEY_HERE")
FRED_RELEASES_URL = "https://api.stlouisfed.org/fred/release/dates"
ONS_CALENDAR_URL = "https://api.beta.ons.gov.uk/v1/releases"

# FRED release IDs — these are the release SERIES (not data series) IDs
# for each economic report. Verify at https://fred.stlouisfed.org/releases
FRED_RELEASE_IDS = {
    "NFP": 50,      # Employment Situation
    "CPI": 10,      # Consumer Price Index
    "PPI": 46,      # Producer Price Index
    "GDP": 53,      # Gross Domestic Product
    "PCE": 54,      # Personal Income and Outlays
}

# How often each release actually happens — used to estimate the next
# date from the last known actual date. GDP is quarterly; the rest are
# monthly.
FRED_RELEASE_CADENCE_DAYS = {
    "NFP": 30,
    "CPI": 30,
    "PPI": 30,
    "GDP": 91,
    "PCE": 30,
}

# AUD release dates have no clean free API — ABS publishes a release
# calendar on their site but not as structured JSON. Update manually
# each month from https://www.abs.gov.au/release-calendar until a
# scraper against their calendar page is built.
AUD_NEXT_RELEASE_MANUAL = "2026-09-17T01:30:00+00:00"  # Labour Force, Australia


def fetch_fred_next_release(release_id, cadence_days=30):
    """
    Get the next release date for a given FRED release ID.

    IMPORTANT DISCOVERY: FRED's release/dates endpoint only returns
    dates when data has ALREADY been published — it's a historical
    log, not a forward-looking calendar. There is no combination of
    parameters that makes it return not-yet-happened dates. So instead
    we take the most recent actual date and estimate the next one
    using the release's typical cadence (monthly releases ~30 days,
    GDP quarterly ~91 days — pass cadence_days accordingly).

    This is an ESTIMATE, not a confirmed date. The prediction/scheduler
    logic should treat it as approximate — good enough to trigger the
    24h-before publish window in the right general timeframe, but not
    precise to the hour/day the way a real BLS/BEA calendar would be.
    """
    params = {
        "release_id": release_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "include_release_dates_with_no_data": "false",
        "sort_order": "desc",  # most recent first — we just need the latest
        "limit": 1,
    }
    resp = requests.get(FRED_RELEASES_URL, params=params, timeout=10)
    resp.raise_for_status()
    dates = resp.json().get("release_dates", [])

    if not dates:
        print(f"[calendar] release_id={release_id}: no historical dates found at all")
        return None

    last_actual = datetime.strptime(dates[0]["date"], "%Y-%m-%d").date()
    estimated_next = last_actual + timedelta(days=cadence_days)

    # If our estimate is somehow still in the past (e.g. cadence_days
    # too short), keep adding the interval until it's in the future.
    today = datetime.now(timezone.utc).date()
    while estimated_next < today:
        estimated_next += timedelta(days=cadence_days)

    print(f"[calendar] release_id={release_id}: last actual={last_actual}, "
          f"estimated next={estimated_next}")

    return f"{estimated_next.isoformat()}T12:30:00+00:00"


def fetch_ons_next_cpi_release():
    """
    CONFIRMED BROKEN via live testing: api.beta.ons.gov.uk returns 404
    for this query shape — ONS has restructured their API since this
    was written. Falls back to manual entry, same pattern as AUD,
    until someone finds ONS's current working endpoint structure.
    UK CPI is released monthly, typically in the third week.
    """
    return None  # forces the manual fallback in build_release_schedule()


# GBP CPI release dates have no working automated source right now —
# update manually from https://www.ons.gov.uk/releasecalendar
GBP_CPI_NEXT_RELEASE_MANUAL = "2026-09-16T06:00:00+00:00"


def build_release_schedule():
    """
    Returns the same shape rebuild_all_predictions() in api_server.py
    expects: {indicator_name: iso_timestamp}
    """
    schedule = {}

    for indicator, release_id in FRED_RELEASE_IDS.items():
        try:
            cadence = FRED_RELEASE_CADENCE_DAYS.get(indicator, 30)
            schedule[indicator] = fetch_fred_next_release(release_id, cadence_days=cadence)
        except Exception as e:
            print(f"[warning] FRED calendar fetch failed for {indicator}: {e}")
            schedule[indicator] = None

    try:
        gbp_result = fetch_ons_next_cpi_release()
        schedule["GBP CPI"] = gbp_result if gbp_result else GBP_CPI_NEXT_RELEASE_MANUAL
    except Exception as e:
        print(f"[warning] ONS calendar fetch failed: {e}")
        schedule["GBP CPI"] = GBP_CPI_NEXT_RELEASE_MANUAL

    schedule["AUD Employment"] = AUD_NEXT_RELEASE_MANUAL

    return schedule


if __name__ == "__main__":
    schedule = build_release_schedule()
    print(json.dumps(schedule, indent=2))
    with open("release_schedule.json", "w") as f:
        json.dump(schedule, f, indent=2)

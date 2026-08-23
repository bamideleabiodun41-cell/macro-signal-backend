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

# AUD release dates have no clean free API — ABS publishes a release
# calendar on their site but not as structured JSON. Update manually
# each month from https://www.abs.gov.au/release-calendar until a
# scraper against their calendar page is built.
AUD_NEXT_RELEASE_MANUAL = "2026-09-17T01:30:00+00:00"  # Labour Force, Australia


def fetch_fred_next_release(release_id):
    """
    Get the next upcoming release date for a given FRED release ID.

    Deliberately does NOT set realtime_start/realtime_end — testing
    showed that explicitly requesting a future window returns zero
    results (likely because these params scope which vintage of the
    release-dates record to view, not which calendar dates to return).
    The plain default call, sorted ascending with no realtime override,
    is the standard approach used by third-party FRED clients and
    returns the full history including known future scheduled dates.
    """
    params = {
        "release_id": release_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "include_release_dates_with_no_data": "false",
        "sort_order": "asc",
        "limit": 1000,
    }
    resp = requests.get(FRED_RELEASES_URL, params=params, timeout=10)
    resp.raise_for_status()
    dates = resp.json().get("release_dates", [])

    print(f"[calendar debug] release_id={release_id} got {len(dates)} dates, "
          f"sample={dates[:3] if dates else 'EMPTY'}, "
          f"last3={dates[-3:] if dates else 'EMPTY'}")

    now = datetime.now(timezone.utc).date()
    for entry in dates:
        release_date = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        if release_date >= now:
            # FRED gives date only, not time — US releases are
            # conventionally 8:30am ET (12:30 or 13:30 UTC depending
            # on daylight saving). Flagged as an approximation.
            return f"{entry['date']}T12:30:00+00:00"
    return None


def fetch_ons_next_cpi_release():
    """
    ONS beta API lists upcoming releases. Filters for CPI-related
    releases. ONS release names shift wording occasionally — this
    does a loose substring match rather than relying on an exact ID.
    """
    params = {"query": "consumer price inflation", "upcoming": "true"}
    resp = requests.get(ONS_CALENDAR_URL, params=params, timeout=10)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return None
    # Take the soonest matching release
    soonest = sorted(items, key=lambda x: x.get("release_date", ""))[0]
    return soonest.get("release_date")


def build_release_schedule():
    """
    Returns the same shape rebuild_all_predictions() in api_server.py
    expects: {indicator_name: iso_timestamp}
    """
    schedule = {}

    for indicator, release_id in FRED_RELEASE_IDS.items():
        try:
            schedule[indicator] = fetch_fred_next_release(release_id)
        except Exception as e:
            print(f"[warning] FRED calendar fetch failed for {indicator}: {e}")
            schedule[indicator] = None

    try:
        schedule["GBP CPI"] = fetch_ons_next_cpi_release()
    except Exception as e:
        print(f"[warning] ONS calendar fetch failed: {e}")
        schedule["GBP CPI"] = None

    schedule["AUD Employment"] = AUD_NEXT_RELEASE_MANUAL

    return schedule


if __name__ == "__main__":
    schedule = build_release_schedule()
    print(json.dumps(schedule, indent=2))
    with open("release_schedule.json", "w") as f:
        json.dump(schedule, f, indent=2)

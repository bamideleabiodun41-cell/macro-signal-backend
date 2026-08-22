"""
Breaking News + Impact Breakdown Pipeline
-------------------------------------------
Polls news sources, filters for market-moving items, and generates a
plain-language "what this means for X pair" breakdown via the Anthropic API.

Setup:
    1. Free NewsAPI key: https://newsapi.org/register  (or swap in GDELT, no key needed)
    2. Anthropic API key: https://console.anthropic.com
    3. pip install requests anthropic --break-system-packages
    4. Run: python news_pipeline.py
       (intended to run on a loop / cron every 60-120s — see run_forever() at bottom)

Output: appends new items to breaking_news_feed.json, each with:
    - headline, source, timestamp
    - impact_score (0-3, filters noise)
    - affected pairs/currencies
    - plain-language breakdown of how it could move upcoming reports
"""

import os
import json
import time
import requests
from datetime import datetime, timezone

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "YOUR_KEY_HERE")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "YOUR_KEY_HERE")

NEWSAPI_URL = "https://newsapi.org/v2/everything"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
FEED_FILE = "breaking_news_feed.json"

# Keywords that flag a headline as forex-relevant before spending an LLM call on it
IMPACT_KEYWORDS = {
    "high": ["fed", "fomc", "rate hike", "rate cut", "war", "invasion", "resign",
             "resignation", "central bank", "treasury", "sanctions", "default",
             "ceasefire", "coup", "election result", "nfp", "cpi", "inflation report"],
    "medium": ["tariff", "trade deal", "gdp", "jobs report", "oil price", "opec",
               "yield", "bond", "unemployment", "stimulus"],
}

# Geopolitical/event categories — separate from data-release news, these get
# their own tag + currency mapping since the "why it matters" logic differs
# (safe-haven flows, leadership uncertainty, supply shocks) vs. data surprises.
EVENT_CATEGORIES = {
    "war_conflict": {
        "keywords": ["invasion", "airstrike", "attack", "military strike", "war",
                     "conflict escalates", "missile", "troops"],
        "typical_impact": "Safe-haven flows: USD, JPY, CHF, and gold typically strengthen; "
                           "risk-sensitive currencies (AUD, NZD, EM FX) typically weaken.",
    },
    "leadership_change": {
        "keywords": ["resign", "resignation", "steps down", "ousted", "coup",
                     "impeach", "no-confidence vote"],
        "typical_impact": "Political uncertainty typically weakens the domestic currency "
                           "short-term until succession clarity emerges.",
    },
    "central_bank_surprise": {
        "keywords": ["emergency rate", "surprise hike", "surprise cut", "unscheduled meeting",
                     "intervenes", "currency intervention"],
        "typical_impact": "Direct and immediate: surprise hikes strengthen the currency, "
                           "surprise cuts or intervention to weaken it move fast and hard.",
    },
    "sanctions_trade": {
        "keywords": ["sanctions", "export ban", "tariff", "trade war", "embargo"],
        "typical_impact": "Currency of the sanctioned/targeted economy typically weakens; "
                           "safe havens and the sanctioning bloc's currency typically firm.",
    },
    "energy_supply_shock": {
        "keywords": ["opec cuts", "pipeline attack", "oil supply", "strait closure",
                     "refinery attack", "gas supply halt"],
        "typical_impact": "Oil-importing currencies (JPY, INR, EUR) typically weaken; "
                           "commodity/oil-exporting currencies (CAD, NOK, RUB) typically firm.",
    },
}


def classify_event(headline, description):
    """Check if a headline matches a known geopolitical event category."""
    text = f"{headline} {description or ''}".lower()
    for category, info in EVENT_CATEGORIES.items():
        if any(kw in text for kw in info["keywords"]):
            return category, info["typical_impact"]
    return None, None

QUERY_TERMS = "forex OR \"federal reserve\" OR \"central bank\" OR treasury OR inflation OR geopolitical"


def fetch_latest_news(minutes_back=10):
    """Pull recent headlines from NewsAPI."""
    params = {
        "q": QUERY_TERMS,
        "apiKey": NEWSAPI_KEY,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 20,
    }
    resp = requests.get(NEWSAPI_URL, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("articles", [])


def score_impact(headline, description):
    text = f"{headline} {description or ''}".lower()
    if any(kw in text for kw in IMPACT_KEYWORDS["high"]):
        return 3
    if any(kw in text for kw in IMPACT_KEYWORDS["medium"]):
        return 2
    return 0  # below threshold, skip


def generate_breakdown(headline, description, event_category=None, typical_impact=None):
    """
    Call Claude to produce a plain-language impact breakdown.

    Gracefully degrades if ANTHROPIC_API_KEY isn't set (e.g. running
    without billing configured yet) — returns a simpler fallback note
    instead of crashing the whole pipeline, so headlines still show up
    in the News Radar even without the AI layer turned on.
    """
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "YOUR_KEY_HERE":
        if event_category and typical_impact:
            return f"[AI breakdown disabled] {typical_impact}"
        return "[AI breakdown disabled — add ANTHROPIC_API_KEY to enable plain-language analysis]"

    event_context = ""
    if event_category:
        event_context = (
            f"\nThis has been auto-tagged as a '{event_category.replace('_', ' ')}' event. "
            f"General pattern for this event type: {typical_impact} "
            f"Confirm, refine, or override this general pattern based on the specific headline."
        )

    prompt = f"""A forex trader just saw this headline:

Headline: {headline}
Details: {description or 'No further details available.'}
{event_context}

In under 80 words, explain in simple terms:
1. Which currency or currency pair this most directly affects
2. Whether it's likely bullish or bearish for that currency, and why
3. Any upcoming economic report (CPI, NFP, GDP, Fed decision) this could shift expectations for

Be direct and concrete. No hedging filler. If genuinely unclear or not forex-relevant, say so in one line instead."""

    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return "".join(block["text"] for block in data["content"] if block["type"] == "text")
    except Exception as e:
        print(f"[news] breakdown generation failed: {e}")
        if event_category and typical_impact:
            return f"[AI breakdown failed] {typical_impact}"
        return "[AI breakdown temporarily unavailable]"


def load_feed():
    if os.path.exists(FEED_FILE):
        with open(FEED_FILE) as f:
            return json.load(f)
    return []


def save_feed(feed):
    with open(FEED_FILE, "w") as f:
        json.dump(feed, f, indent=2)


def poll_once():
    feed = load_feed()
    seen_urls = {item["url"] for item in feed}
    articles = fetch_latest_news()

    new_items = 0
    for article in articles:
        url = article.get("url")
        if not url or url in seen_urls:
            continue

        headline = article.get("title", "")
        description = article.get("description", "")
        event_category, typical_impact = classify_event(headline, description)
        impact = score_impact(headline, description)

        # A recognized geopolitical event always clears the bar, even if it
        # missed the general keyword scorer.
        if impact == 0 and event_category is None:
            continue

        if event_category and impact < 3:
            impact = 3  # geopolitical events are treated as high-impact by default

        breakdown = generate_breakdown(headline, description, event_category, typical_impact)

        feed.insert(0, {
            "url": url,
            "headline": headline,
            "source": article.get("source", {}).get("name", "unknown"),
            "published_at": article.get("publishedAt"),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "impact_score": impact,
            "event_category": event_category,
            "breakdown": breakdown,
        })
        new_items += 1

    if new_items:
        feed = feed[:200]  # cap feed size
        save_feed(feed)
        print(f"[{datetime.now().isoformat()}] {new_items} new item(s) added")
    else:
        print(f"[{datetime.now().isoformat()}] no new relevant items")

    return new_items


def run_forever(poll_seconds=90):
    """Continuous polling loop. Use a process manager (systemd, pm2) to keep this alive."""
    while True:
        try:
            poll_once()
        except Exception as e:
            print(f"[error] {e}")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    poll_once()
    # Uncomment to run continuously:
    # run_forever(poll_seconds=90)

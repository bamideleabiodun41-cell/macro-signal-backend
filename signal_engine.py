"""
Trade Signal Engine
----------------------
Translates a raw prediction (verdict + confidence + component signals)
into plain-language pair-level trade signals: which pairs/assets,
BUY or SELL, and a confidence percentage.

This is a post-processing layer — it wraps whatever build_prediction()
returns from any of the five predictors, so it doesn't need to be
duplicated inside each one.

IMPORTANT — read before wiring into anything user-facing:
This produces directional signals from macro-data logic, not
individualized investment advice. The UI should carry a visible
disclaimer (e.g. "informational signal, not financial advice") since
displaying BUY/SELL/percentages is the kind of content that invites
people to treat it as a recommendation. That's a UI/legal decision for
you to make, not something this module resolves on its own.
"""

from datetime import datetime, timezone

# For each indicator, maps a verdict to the pairs/assets typically
# affected and the conventional direction. These are general
# macro-to-FX relationships (rate/inflation surprises -> currency
# strength), not backtested signals — treat as a starting framework
# to refine against actual outcomes over time.
PAIR_SIGNAL_MAP = {
    "NFP": {
        "hot":     [("EUR/USD", "SELL", "Strong jobs data supports a hawkish Fed lean, typically dollar-positive"),
                    ("USD/JPY", "BUY",  "Dollar strength typically pairs with yen weakness on rate differentials"),
                    ("Gold",    "SELL", "Strong dollar and firmer real yields typically pressure gold")],
        "soft":    [("EUR/USD", "BUY",  "Weak jobs data supports a dovish Fed lean, typically dollar-negative"),
                    ("USD/JPY", "SELL", "Dollar weakness typically supports yen on narrowing rate differentials"),
                    ("Gold",    "BUY",  "Softer dollar and lower real-yield expectations typically support gold")],
        "in-line": [("EUR/USD", "Moderate", "In-line print unlikely to shift Fed expectations meaningfully"),
                    ("Gold",    "Moderate", "Limited directional catalyst from an in-line print")],
    },
    "CPI": {
        "hot":     [("EUR/USD", "SELL", "Hot inflation raises odds the Fed holds/hikes, typically dollar-positive"),
                    ("Gold",    "SELL", "Sticky inflation without rate-cut relief typically pressures gold")],
        "soft":    [("EUR/USD", "BUY",  "Cool inflation raises odds of Fed easing, typically dollar-negative"),
                    ("Gold",    "BUY",  "Rate-cut expectations typically support gold")],
        "in-line": [("EUR/USD", "Moderate", "In-line print unlikely to shift the policy path"),
                    ("Gold",    "Moderate", "Limited directional catalyst from an in-line print")],
    },
    "PPI": {
        "hot":     [("USD/JPY", "BUY", "Rising producer prices add to inflation-pass-through concerns, typically dollar-supportive")],
        "soft":    [("USD/JPY", "SELL", "Cooling producer prices ease inflation pass-through concerns")],
        "in-line": [("USD/JPY", "Moderate", "Limited standalone catalyst; usually secondary to CPI")],
    },
    "GBP CPI": {
        "hot":     [("GBP/USD", "BUY", "Hot UK inflation supports a hawkish BoE lean, typically GBP-positive")],
        "soft":    [("GBP/USD", "SELL", "Cool UK inflation supports a dovish BoE lean, typically GBP-negative")],
        "in-line": [("GBP/USD", "Moderate", "In-line print unlikely to shift BoE expectations")],
    },
    "AUD Employment": {
        "hot":     [("AUD/USD", "BUY", "Strong labour data supports a hawkish RBA lean, typically AUD-positive")],
        "soft":    [("AUD/USD", "SELL", "Weak labour data supports a dovish RBA lean, typically AUD-negative")],
        "in-line": [("AUD/USD", "Moderate", "In-line print unlikely to shift RBA expectations")],
    },
}

# Plain-language label shown alongside the raw numeric verdict, for
# readers who don't parse ranges/units directly.
SIMPLE_VERDICT_LABEL = {
    "hot": "Likely Bullish (Hawkish)",
    "soft": "Likely Bearish (Dovish)",
    "in-line": "Neutral / Wait for confirmation",
}


def compute_confidence_pct(prediction):
    """
    Derives a 0-100 confidence percentage from the prediction's existing
    confidence tier (high/medium) plus how lopsided the component signals
    were (more weak-vs-strong agreement = higher confidence).
    """
    base = {"high": 75, "medium": 55}.get(prediction.get("confidence"), 50)

    components = prediction.get("components", [])
    signals = [c.get("signal") for c in components if c.get("signal") in ("weak", "strong")]
    if signals:
        majority_signal = max(set(signals), key=signals.count)
        agreement_ratio = signals.count(majority_signal) / len(signals)
        base += int((agreement_ratio - 0.5) * 30)  # nudge up/down based on agreement

    return max(30, min(90, base))  # never claim more than 90% or less than 30%


def attach_trade_signals(prediction):
    """
    Takes a prediction dict (as returned by any predictor's
    build_prediction()) and adds:
        - trade_signals: list of {pair, direction, confidence_pct, rationale}
        - simple_verdict: plain-language label for non-technical readers

    Mutates and returns the same dict so it can be used as a drop-in
    wrapper: `prediction = attach_trade_signals(rescore_fn())`
    """
    indicator = prediction.get("indicator")
    verdict = prediction.get("verdict")
    confidence_pct = compute_confidence_pct(prediction)

    signal_templates = PAIR_SIGNAL_MAP.get(indicator, {}).get(verdict, [])

    prediction["trade_signals"] = [
        {
            "pair": pair,
            "direction": direction,
            "confidence_pct": confidence_pct,
            "rationale": rationale,
        }
        for pair, direction, rationale in signal_templates
    ]
    prediction["simple_verdict"] = SIMPLE_VERDICT_LABEL.get(verdict, "Unclear")
    prediction["signals_generated_at"] = datetime.now(timezone.utc).isoformat()

    return prediction

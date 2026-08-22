# Macro Signal — Backend

Predicts NFP, CPI, PPI, GDP, PCE (USD), GBP CPI, and AUD Employment 24 hours
before each release, revises live if breaking news hits, and tracks accuracy
after each report drops.

## Files

| File | Role |
|---|---|
| `api_server.py` | Flask API + background polling loop (entry point) |
| `nfp_predictor.py`, `cpi_predictor.py`, `ppi_predictor.py`, `gdp_predictor.py`, `pce_predictor.py`, `gbp_cpi_predictor.py`, `aud_employment_predictor.py` | One scorecard model per indicator |
| `news_pipeline.py` | Breaking news ingestion + LLM impact breakdown |
| `revision_engine.py` | Matches breaking news to live predictions, triggers rescore |
| `scheduler.py` | Auto-publishes 24h before release, marks released after |
| `calendar_feed.py` | Live release-date pull (FRED / ONS) |
| `signal_engine.py` | Converts verdict -> pair-level BUY/SELL + confidence % |
| `performance_tracker.py` | Actual-vs-predicted scoring after release |

## Environment variables (set these in Render, not in this repo)

- `FRED_API_KEY` — https://fred.stlouisfed.org/docs/api/api_key.html
- `NEWSAPI_KEY` — https://newsapi.org/register
- `ANTHROPIC_API_KEY` — https://console.anthropic.com

## Local run

```bash
pip install -r requirements.txt
export FRED_API_KEY=...
export NEWSAPI_KEY=...
export ANTHROPIC_API_KEY=...
python api_server.py
```

## Deploy

See `render.yaml` — Render reads it automatically via Blueprint deploy.

## Known limitations (v1)

- Login/auth not yet implemented on the frontend
- GBP CPI / AUD Employment actuals not wired into performance_tracker.py
- ISM services employment, Manheim index, UK petrol, Ofgem cap direction,
  AUD labour force figures are manual-entry constants — update periodically

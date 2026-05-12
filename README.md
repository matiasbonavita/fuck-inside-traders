# Fuck Inside Traders

Local Python anomaly radar for comparing prediction-market movement, related real-world asset prices, and public headlines.

This is not an insider-trading accusation tool. It is not a real-money trading bot. V1 is dry-run / paper-only and produces alerts, timelines, and analysis for manual review.

## What It Does

- Collects prediction-market snapshots from public/free sources, with local fallback data.
- Collects related asset prices through a replaceable provider abstraction, using `yfinance` for MVP.
- Collects public headlines from GDELT and RSS feeds.
- Scores deterministic anomaly signals in Python.
- Creates anomaly events when scores cross configured thresholds.
- Logs dry-run Telegram-style alerts to `logs/app.log`.
- Shows recent events, snapshots, headlines, and paper-trade placeholders in Streamlit.
- Labels each alert by data provenance so mock-backed demo alerts cannot look like live anomalies.

## Setup

```bash
make setup
cp .env.example .env
make db
```

The Makefile defaults to `python3.12`, creates a local `.venv`, and runs app commands through that virtual environment. Override with `make PYTHON=/path/to/python3.12 setup` if needed.

The default `.env.example` points at the local Postgres service from `docker-compose.yml`. Tests use isolated SQLite databases.

## Run

```bash
make collect-once
make detect-once
make analyst-once
make monitor
make analyst
make review-polymarket TOPIC=iran_oil
make dashboard
make logs
```

`make collect-once` and `make detect-once` create tables automatically. If public APIs fail or return an unexpected shape, collectors log the failure and use deterministic fallback data so the app keeps running locally.

`make monitor` runs a local dry-run loop: collect public data, run the detector, sleep, and repeat. Use `make monitor INTERVAL=2` to change the interval in minutes. The loop never executes trades.

`make analyst-once` processes existing `AnomalyEvent` rows that do not yet have an analyst report for the configured backend. The default backend is deterministic and local. `make analyst` runs the same reporting loop on a schedule.

## Local Demo Workflows

These commands are local-only and are intended for development data hygiene:

```bash
make db-reset
make seed-demo
make clean-demo
make backfill-provenance
```

- `make db-reset` drops and recreates local tables.
- `make seed-demo` inserts deterministic mock data and creates a mock-backed anomaly event.
- `make clean-demo` removes mock/synthetic/unknown demo rows.
- `make backfill-provenance` labels older anomaly events as `UNKNOWN` or `MOCK-BACKED`.

Demo and mock data is never a real anomaly candidate.

## Topic Configuration

Topic config lives in `config/topics.yaml`. For Polymarket discovery, prefer explicit narrow queries:

```yaml
polymarket_queries:
  - iran oil
  - hormuz oil
  - hormuz
polymarket_allowlist:
  - known-market-id-or-slug
polymarket_blocklist:
  - bad-market-id-or-slug
```

The collector prefers curated watchlisted markets from `config/polymarket_watchlist.yaml` before broad discovery:

```yaml
topics:
  iran_oil:
    markets:
      - slug: strait-of-hormuz-traffic-returns-to-normal-by-may-15
        external_id: "2054133"
        url: https://polymarket.com/event/strait-of-hormuz-traffic-returns-to-normal-by-may-15
        description: Curated live Hormuz market for validating live ingestion.
        active: true
```

Watchlist entries may use a slug, external ID, or URL. When `external_id` is present, FIT fetches the exact Polymarket market endpoint first, then falls back to slug/event lookups. If a watchlisted market cannot be fetched, FIT logs a warning and continues.

For non-watchlist discovery, FIT uses Polymarket `/public-search` with the configured queries, deduplicates by external ID, applies blocklist first, accepts allowlisted markets, and then applies deterministic relevance scoring. The scorer uses title/question, description, slug, tags, category, outcomes, topic keywords, configured negative keywords, and relevant categories from `config/thresholds.yaml`.

Every reviewed Polymarket candidate is stored locally with accepted/rejected status, score, and reason so the dashboard can explain why a market was accepted or rejected.

Use the candidate review helper before promoting markets into the watchlist:

```bash
python -m fuck_inside_traders.scripts.review_polymarket_candidates --topic iran_oil
```

The output includes topic, query, accepted flag, active/closed state, relevance score, rejection reason, external ID, slug, and title. Promotion is manual: copy a relevant active/open slug into `config/polymarket_watchlist.yaml` with a description and `active: true`.

Closed, inactive, or unfetched watchlist entries are stored as rejected discovery candidates and shown separately in the dashboard. They are not treated as live prediction-market candidates.

When a live Polymarket collection succeeds, older active Polymarket markets for the same topic that are absent from the accepted live set are marked inactive locally. This keeps retired watchlist entries from looking current in the dashboard.

## Telegram Alerts

Dry-run is enabled by default.

Real Telegram delivery is only attempted when:

- `DRY_RUN=false`
- `TELEGRAM_BOT_TOKEN` is set
- `TELEGRAM_CHAT_ID` is set

Missing Telegram credentials do not crash the app.

## Analyst / Hermes Readiness

The detector remains deterministic Python and is the only component that decides whether an anomaly event exists. The analyst layer starts only after an `AnomalyEvent` exists.

Configured defaults:

```bash
ANALYST_BACKEND=deterministic
HERMES_ENABLED=false
HERMES_ENDPOINT=
HERMES_TIMEOUT_SECONDS=20
```

Analyst context is assembled as a structured Pydantic schema with event fields, market metadata, score breakdown, prediction snapshots, asset snapshots, headline timeline, provider health, and provenance. Analyst reports are persisted in `analyst_reports`.

Available backends:

- `deterministic`: default local backend. Produces a structured report and safe summary text.
- `hermes`: Hermes-ready backend stub. It validates and packages the same context, but does not make a network call unless `HERMES_ENABLED=true` and `HERMES_ENDPOINT` is configured.

Hermes must remain an optional reporting layer. It must not create anomaly events, override scores, execute trades, choose position sizes, identify suspects, or make accusations.

## Provenance Labels

Every new anomaly explanation includes a provenance label:

- `LIVE`: all required signal components used live provider data.
- `PARTIAL-LIVE`: at least one required component used live data and another was missing, unknown, or fallback.
- `MOCK-BACKED`: core movement depends on mock, fallback, or synthetic baseline data.
- `UNKNOWN`: event predates provenance tracking or has incomplete source metadata.

`MOCK-BACKED` and `UNKNOWN` alerts are for local testing or historical review only. They are not real anomaly candidates.

Snapshot rows store `source` and `provider_kind` metadata. The dashboard shows latest collector status, per-snapshot provider kinds, and the anomaly provenance label.

No alert should be treated as a review candidate unless:

- Prediction-market data is `LIVE`.
- Asset data is `LIVE`.
- Headline data is `LIVE` or clearly `PARTIAL-LIVE` with visible source details.

`MOCK-BACKED` alerts are local demo/testing artifacts only.

## Headline Collection Notes

GDELT requests are narrowed to fewer query terms and lower record counts. `429 Too Many Requests` responses are logged as rate limits, not crashes. Non-JSON responses log content type and a short preview, then continue. Set `GDELT_ENABLED=false` in `.env` to disable GDELT locally if it is too noisy.

RSS feeds are best-effort; parse failures are recorded in provider health and collectors continue. The default feed list includes general world news plus energy-focused RSS sources.

## Dashboard Interpretation

The dashboard starts with "Live Monitoring State":

- Live prediction-market count.
- Live asset snapshot count.
- Live headline count.
- Latest detector result.
- Latest alert label.
- Fresh/stale age indicators from `config/thresholds.yaml`.

"Live data collected, no anomaly detected" means the live data path is working but deterministic scores stayed below the alert threshold.

"Live Market Signal Review" shows current deterministic score components for active live Polymarket markets even when no anomaly event is created. Use it to see whether a market is below threshold, waiting for more live snapshots, or missing related asset movement in the scoring window.

The news table is labeled "Headline Sources For Topic" / "Headline Timeline For Topic" because headlines come from RSS/GDELT, not Polymarket.

"Analyst Reports" shows saved deterministic or Hermes report status, backend, provenance label, and summary text. These reports are manual-review aids only.

## Safety

- No real-money trading execution exists.
- No brokerage APIs are used.
- No secrets are stored in code.
- Detection is deterministic Python logic.
- The analyst layer in `fuck_inside_traders/reports/analyst.py` is optional reporting only; Hermes is disabled by default.

## Development

```bash
make test
make lint
```

Git identity is configured locally for this repository only:

```bash
git config --local user.name
git config --local user.email
```

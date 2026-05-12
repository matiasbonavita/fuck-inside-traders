# FIT V1 Plan

## Completed

- Initialized local git configuration for this repo only.
- Added GitHub origin for `matiasbonavita/fuck-inside-traders` without pushing.
- Created the project scaffold, config files, docs, package directories, and logs directory.
- Added SQLAlchemy models, indexes, session helpers, and FastAPI health app.
- Added Polymarket, yfinance, and GDELT/RSS collectors with provider abstractions.
- Added fallback providers and first-run baseline seeding for local dry-run operation.
- Added deterministic scoring for odds jumps, volume spikes, asset confirmation, headline gaps, and combined signals.
- Added anomaly event creation, duplicate suppression, analyst summary stub, and dry-run Telegram alert formatting.
- Added Streamlit dashboard for latest anomalies, score breakdowns, market snapshots, asset charts, news, and paper-trade placeholders.
- Added focused tests for model persistence, scoring, collectors, detector integration, and alerts.
- Verified local commands:
  - `make setup`
  - `make db`
  - `make test`
  - `make lint`
  - `make collect-once`
  - `make detect-once`
  - `make dashboard`
  - `make logs`
- Added snapshot-level provenance metadata for prediction markets, asset prices, and news.
- Added provider health rows for collector runs.
- Added alert labels: `LIVE`, `PARTIAL-LIVE`, and `MOCK-BACKED`.
- Updated dry-run Telegram formatting so mock-backed alerts are marked demo-only.
- Tightened Polymarket market relevance with include/exclude keywords and deterministic scoring.
- Narrowed GDELT queries, added 429 backoff handling, and made RSS failures less noisy.
- Added dashboard provenance and provider health visibility.
- Added tests for relevance filtering, GDELT 429 handling, provider-kind metadata, and alert provenance.
- Added topic-level Polymarket queries, allowlist, and blocklist config.
- Changed Polymarket discovery to query multiple narrow terms and deduplicate results.
- Added local DB maintenance commands: `db-reset`, `seed-demo`, `clean-demo`, and `backfill-provenance`.
- Added detector run status rows.
- Updated dashboard to show provider trust signals first and separate review candidates from demo/mock/unknown events.
- Added tests for Polymarket allowlist and blocklist behavior.
- Added curated Polymarket watchlist config at `config/polymarket_watchlist.yaml`.
- Moved live Polymarket discovery from naive `/markets?search=...` toward watchlist-first `/events/slug/...` and `/public-search?q=...` discovery.
- Added richer Polymarket relevance scoring across question/title, description, slug, tags, category, outcomes, positive topic keywords, negative keywords, and relevant categories.
- Added persisted Polymarket discovery candidates with accepted/rejected status, relevance score, and rejection reason.
- Updated collector status/logging so mock fallback is explicit with candidate counts, rejection reasons, fallback attempt status, and mock usage.
- Updated dashboard to show accepted live Polymarket markets and rejected candidates.
- Verified one live Polymarket watchlist candidate is accepted locally: `Strait of Hormuz traffic returns to normal by May 15?`.
- Added tests for watchlist parsing, watchlist fetch, positive/negative relevance, no-live/no-mock status, and live event provenance.
- Expanded the curated `iran_oil` Polymarket watchlist with additional Hormuz and OPEC markets.
- Added the `review_polymarket_candidates` helper script and `make review-polymarket TOPIC=iran_oil`.
- Added closed/inactive/unfetched watchlist visibility through persisted rejected discovery candidates.
- Added GDELT non-JSON content-type/preview logging and a `GDELT_ENABLED` config flag.
- Added EIA and OilPrice RSS feeds for more energy headline coverage.
- Added freshness thresholds for prediction markets, asset prices, headlines, and detector state.
- Added detector protection to skip creating `LIVE` alerts when core data is stale.
- Added dashboard "Live Monitoring State", headline source counts, explicit headline timeline labeling, and stale watchlist grouping.
- Added tests for closed/inactive watchlist entries, GDELT non-JSON status, and stale live alert suppression.

## Remaining

- Keep watchlisted Polymarket slugs fresh as markets close or resolve.
- Keep adding curated Polymarket market IDs/slugs after manual review.
- Add Kalshi provider behind the existing prediction-market abstraction.
- Add official-source headline providers.
- Add paper-trade simulation logic on top of the existing `PaperTrade` model.
- Optionally back the deterministic analyst interface with Hermes after V1 remains stable.
- Add persistent migrations with Alembic once the schema stabilizes.

## Guardrails

- This app is a dry-run / paper-only anomaly radar.
- It must not execute real-money trades or connect to brokerage APIs.
- Alerts and reports must avoid accusations and personal identification.
- Secrets belong in local environment variables, never in source control.
- No alert is reviewable unless prediction-market data is live, asset data is live, and headline data is live or explicitly partial-live with source details.

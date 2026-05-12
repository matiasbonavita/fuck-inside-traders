from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from fuck_inside_traders.collectors.news import had_matching_headline_before
from fuck_inside_traders.config import load_thresholds, load_topics
from fuck_inside_traders.detectors.scoring import (
    combine_signal_score,
    compute_asset_confirmation,
    compute_headline_gap,
    compute_odds_jump,
    compute_volume_spike,
)
from fuck_inside_traders.monitoring import age_minutes, is_stale
from fuck_inside_traders.provenance import ALERT_MOCK_BACKED, ALERT_UNKNOWN, anomaly_event_label
from fuck_inside_traders.storage.database import init_db, session_scope
from fuck_inside_traders.storage.models import (
    AnalystReport,
    AnomalyEvent,
    AssetPriceSnapshot,
    CollectorStatus,
    Market,
    NewsItem,
    PaperTrade,
    PolymarketDiscoveryCandidate,
    PredictionMarketSnapshot,
)
from fuck_inside_traders.time_utils import ensure_utc

st.set_page_config(page_title="Fuck Inside Traders", layout="wide")


def _latest_prior_headline_gap_minutes(
    session: Session,
    topic: str,
    before_timestamp: datetime,
    lookback_minutes: int,
) -> float | None:
    before_timestamp = ensure_utc(before_timestamp)
    latest = session.scalar(
        select(NewsItem.published_at)
        .where(
            NewsItem.topic == topic,
            NewsItem.published_at < before_timestamp,
            NewsItem.published_at
            >= before_timestamp - timedelta(minutes=lookback_minutes),
        )
        .order_by(desc(NewsItem.published_at))
        .limit(1)
    )
    if latest is None:
        return None
    return max(0.0, (before_timestamp - ensure_utc(latest)).total_seconds() / 60.0)


def _load_live_market_signal_rows(
    session: Session,
    topics_by_name: dict[str, Any],
    thresholds: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    markets = list(
        session.scalars(
            select(Market)
            .where(Market.active.is_(True), Market.source == "polymarket")
            .order_by(Market.topic, Market.title)
            .limit(100)
        )
    )
    alert_threshold = float(thresholds.alert_threshold)
    for market in markets:
        topic = topics_by_name.get(market.topic)
        snapshots = list(
            session.scalars(
                select(PredictionMarketSnapshot)
                .where(PredictionMarketSnapshot.market_id == market.id)
                .order_by(PredictionMarketSnapshot.timestamp.asc())
                .limit(500)
            )
        )
        live_snapshot_count = sum(
            1 for snapshot in snapshots if snapshot.provider_kind == "live"
        )
        latest_snapshot = max(
            (ensure_utc(snapshot.timestamp) for snapshot in snapshots),
            default=None,
        )
        base_row: dict[str, Any] = {
            "market_id": market.id,
            "topic": market.topic,
            "market": market.title,
            "live_snapshots": live_snapshot_count,
            "total_snapshots": len(snapshots),
            "latest_snapshot": latest_snapshot,
            "signal_score": None,
            "alert_threshold": alert_threshold,
            "odds_score": None,
            "volume_score": None,
            "asset_score": None,
            "headline_score": None,
            "odds_jump": None,
            "volume_z": None,
            "asset_moves": "",
            "headline_gap_minutes": None,
            "why_no_alert": "",
        }
        if topic is None:
            rows.append({**base_row, "status": "unconfigured_topic"})
            continue
        if len(snapshots) < 2:
            rows.append(
                {
                    **base_row,
                    "status": "insufficient_snapshots",
                    "why_no_alert": "Need at least two prediction snapshots.",
                }
            )
            continue

        odds_result = compute_odds_jump(snapshots, thresholds.windows_minutes)
        volume_result = compute_volume_spike(snapshots)
        asset_snapshots_by_symbol: dict[str, list[AssetPriceSnapshot]] = {}
        for symbol in topic.assets:
            asset_snapshots_by_symbol[symbol] = list(
                session.scalars(
                    select(AssetPriceSnapshot)
                    .where(
                        AssetPriceSnapshot.topic == topic.name,
                        AssetPriceSnapshot.symbol == symbol,
                    )
                    .order_by(AssetPriceSnapshot.timestamp.asc())
                    .limit(500)
                )
            )
        asset_result = compute_asset_confirmation(
            asset_snapshots_by_symbol,
            odds_result.window_minutes or max(thresholds.windows_minutes),
        )
        headline_lookback = max(thresholds.windows_minutes)
        had_headline = had_matching_headline_before(
            market.topic,
            odds_result.started_at,
            headline_lookback,
            session=session,
        )
        prior_gap = _latest_prior_headline_gap_minutes(
            session,
            market.topic,
            odds_result.started_at,
            headline_lookback,
        )
        headline_result = compute_headline_gap(had_headline, headline_lookback, prior_gap)
        signal_score = combine_signal_score(
            odds_result.score,
            volume_result.score,
            asset_result.score,
            headline_result.score,
            topic.topic_importance,
        )
        reasons = []
        status = "below_threshold"
        if signal_score >= alert_threshold:
            status = "at_or_above_threshold"
        else:
            reasons.append(f"Signal {signal_score:.2f} is below {alert_threshold:.2f}.")
        if live_snapshot_count < 2:
            reasons.append("Needs more live prediction-market history.")
        if not asset_result.moves:
            reasons.append("No related asset movement available for the scoring window.")
        rows.append(
            {
                **base_row,
                "status": status,
                "signal_score": signal_score,
                "odds_score": odds_result.score,
                "volume_score": volume_result.score,
                "asset_score": asset_result.score,
                "headline_score": headline_result.score,
                "odds_jump": odds_result.odds_jump,
                "volume_z": volume_result.z_score,
                "asset_moves": ", ".join(
                    f"{symbol} {move:+.2%}" for symbol, move in asset_result.moves.items()
                ),
                "headline_gap_minutes": headline_result.gap_minutes,
                "why_no_alert": " ".join(reasons) or "Score is at or above threshold.",
            }
        )
    return rows


@st.cache_data(ttl=30)
def load_dashboard_data() -> dict[str, pd.DataFrame]:
    init_db()
    thresholds = load_thresholds()
    now = datetime.now(UTC)
    with session_scope() as session:
        events = list(
            session.scalars(select(AnomalyEvent).order_by(desc(AnomalyEvent.created_at)).limit(25))
        )
        markets = {market.id: market for market in session.scalars(select(Market)).all()}
        review_events = [
            event
            for event in events
            if anomaly_event_label(event) not in {ALERT_MOCK_BACKED, ALERT_UNKNOWN}
        ]
        latest_event = review_events[0] if review_events else None
        topics = load_topics()
        topics_by_name = {topic.name: topic for topic in topics}
        fallback_topic = topics[0].name if topics else None
        display_topic = (
            latest_event.topic
            if latest_event
            else events[0].topic
            if events
            else fallback_topic
        )
        market_snapshots = []
        asset_snapshots = []
        news_items = []
        paper_trades = []
        statuses = list(
            session.scalars(
                select(CollectorStatus).order_by(desc(CollectorStatus.created_at)).limit(20)
            )
        )
        polymarket_candidates = list(
            session.scalars(
                select(PolymarketDiscoveryCandidate)
                .order_by(desc(PolymarketDiscoveryCandidate.created_at))
                .limit(100)
            )
        )
        analyst_reports = list(
            session.scalars(select(AnalystReport).order_by(desc(AnalystReport.created_at)).limit(50))
        )

        if latest_event:
            market_snapshots = list(
                session.scalars(
                    select(PredictionMarketSnapshot)
                    .where(PredictionMarketSnapshot.market_id == latest_event.market_id)
                    .order_by(PredictionMarketSnapshot.timestamp.asc())
                )
            )
            asset_snapshots = list(
                session.scalars(
                    select(AssetPriceSnapshot)
                    .where(AssetPriceSnapshot.topic == latest_event.topic)
                    .order_by(AssetPriceSnapshot.timestamp.asc())
                )
            )
            paper_trades = list(
                session.scalars(
                    select(PaperTrade)
                    .where(PaperTrade.anomaly_event_id == latest_event.id)
                    .order_by(PaperTrade.created_at.desc())
                )
            )
        if display_topic:
            news_items = list(
                session.scalars(
                    select(NewsItem)
                    .where(NewsItem.topic == display_topic)
                    .order_by(NewsItem.published_at.asc())
                    .limit(50)
                )
            )

        latest_prediction_timestamp = session.scalar(
            select(func.max(PredictionMarketSnapshot.timestamp)).where(
                PredictionMarketSnapshot.provider_kind == "live"
            )
        )
        latest_asset_timestamp = session.scalar(
            select(func.max(AssetPriceSnapshot.timestamp)).where(
                AssetPriceSnapshot.provider_kind == "live"
            )
        )
        latest_headline_timestamp = session.scalar(
            select(func.max(NewsItem.published_at)).where(NewsItem.provider_kind == "live")
        )
        live_prediction_cutoff = now - timedelta(
            minutes=thresholds.prediction_market_fresh_minutes
        )
        live_asset_cutoff = now - timedelta(minutes=thresholds.asset_price_fresh_minutes)
        live_headline_cutoff = now - timedelta(minutes=thresholds.headline_fresh_minutes)
        live_prediction_count = session.scalar(
            select(func.count(func.distinct(PredictionMarketSnapshot.market_id))).where(
                PredictionMarketSnapshot.provider_kind == "live",
                PredictionMarketSnapshot.timestamp >= live_prediction_cutoff,
            )
        )
        live_asset_count = session.scalar(
            select(func.count()).select_from(AssetPriceSnapshot).where(
                AssetPriceSnapshot.provider_kind == "live",
                AssetPriceSnapshot.timestamp >= live_asset_cutoff,
            )
        )
        live_headline_count = session.scalar(
            select(func.count()).select_from(NewsItem).where(
                NewsItem.provider_kind == "live",
                NewsItem.published_at >= live_headline_cutoff,
            )
        )
        headline_source_counts = list(
            session.execute(
                select(NewsItem.source, NewsItem.provider_kind, func.count())
                .group_by(NewsItem.source, NewsItem.provider_kind)
                .order_by(NewsItem.source)
            )
        )
        latest_detector_status = next(
            (status for status in statuses if status.data_type == "detector"),
            None,
        )
        live_market_signal_rows = _load_live_market_signal_rows(
            session,
            topics_by_name,
            thresholds,
        )

        return {
            "monitoring_state": pd.DataFrame(
                [
                    {
                        "live_prediction_markets": live_prediction_count or 0,
                        "live_asset_snapshots": live_asset_count or 0,
                        "live_headlines": live_headline_count or 0,
                        "latest_detector_result": latest_detector_status.status
                        if latest_detector_status
                        else "unknown",
                        "latest_alert_label": anomaly_event_label(events[0]) if events else "none",
                        "prediction_age_minutes": age_minutes(latest_prediction_timestamp, now),
                        "prediction_stale": is_stale(
                            latest_prediction_timestamp,
                            thresholds.prediction_market_fresh_minutes,
                            now,
                        ),
                        "asset_age_minutes": age_minutes(latest_asset_timestamp, now),
                        "asset_stale": is_stale(
                            latest_asset_timestamp,
                            thresholds.asset_price_fresh_minutes,
                            now,
                        ),
                        "headline_age_minutes": age_minutes(latest_headline_timestamp, now),
                        "headline_stale": is_stale(
                            latest_headline_timestamp,
                            thresholds.headline_fresh_minutes,
                            now,
                        ),
                    }
                ]
            ),
            "events": pd.DataFrame(
                [
                    {
                        "id": event.id,
                        "topic": event.topic,
                        "provenance": anomaly_event_label(event),
                        "review_candidate": anomaly_event_label(event)
                        not in {ALERT_MOCK_BACKED, ALERT_UNKNOWN},
                        "market": markets.get(event.market_id).title
                        if event.market_id in markets
                        else "Unknown",
                        "signal_score": event.signal_score,
                        "odds_jump": event.odds_jump,
                        "volume_z_score": event.volume_z_score,
                        "asset_confirmation_score": event.asset_confirmation_score,
                        "headline_gap_minutes": event.headline_gap_minutes,
                        "created_at": event.created_at,
                    }
                    for event in events
                ]
            ),
            "market_snapshots": pd.DataFrame(
                [
                    {
                        "timestamp": snapshot.timestamp,
                        "probability": snapshot.probability,
                        "volume": snapshot.volume,
                        "source": snapshot.source,
                        "provider_kind": snapshot.provider_kind,
                    }
                    for snapshot in market_snapshots
                ]
            ),
            "asset_snapshots": pd.DataFrame(
                [
                    {
                        "timestamp": snapshot.timestamp,
                        "symbol": snapshot.symbol,
                        "price": snapshot.price,
                        "source": snapshot.source,
                        "provider_kind": snapshot.provider_kind,
                    }
                    for snapshot in asset_snapshots
                ]
            ),
            "news": pd.DataFrame(
                [
                    {
                        "published_at": item.published_at,
                        "source": item.source,
                        "provider_kind": item.provider_kind,
                        "title": item.title,
                        "url": item.url,
                    }
                    for item in news_items
                ]
            ),
            "paper_trades": pd.DataFrame(
                [
                    {
                        "symbol": trade.symbol,
                        "direction": trade.direction,
                        "entry_price": trade.entry_price,
                        "entry_time": trade.entry_time,
                        "exit_price": trade.exit_price,
                        "exit_time": trade.exit_time,
                        "pnl": trade.pnl,
                        "status": trade.status,
                    }
                    for trade in paper_trades
                ]
            ),
            "collector_statuses": pd.DataFrame(
                [
                    {
                        "created_at": status.created_at,
                        "provider": status.provider,
                        "data_type": status.data_type,
                        "status": status.status,
                        "provider_kind": status.provider_kind,
                        "message": status.message,
                    }
                    for status in statuses
                ]
            ),
            "polymarket_candidates": pd.DataFrame(
                [
                    {
                        "created_at": candidate.created_at,
                        "topic": candidate.topic,
                        "query": candidate.query,
                        "accepted": candidate.accepted,
                        "score": candidate.relevance_score,
                        "reason": candidate.rejection_reason or "accepted",
                        "provider_kind": candidate.provider_kind,
                        "active": candidate.active,
                        "closed": candidate.closed,
                        "title": candidate.title,
                        "slug": candidate.slug,
                        "external_id": candidate.external_id,
                        "url": candidate.url,
                    }
                    for candidate in polymarket_candidates
                ]
            ),
            "headline_source_counts": pd.DataFrame(
                [
                    {
                        "source": source,
                        "provider_kind": provider_kind,
                        "count": count,
                    }
                    for source, provider_kind, count in headline_source_counts
                ]
            ),
            "live_market_scores": pd.DataFrame(live_market_signal_rows),
            "analyst_reports": pd.DataFrame(
                [
                    {
                        "created_at": report.created_at,
                        "event_id": report.anomaly_event_id,
                        "topic": report.anomaly_event.topic
                        if report.anomaly_event
                        else "unknown",
                        "market": report.anomaly_event.market.title
                        if report.anomaly_event and report.anomaly_event.market
                        else "unknown",
                        "backend": report.backend,
                        "status": report.status,
                        "provenance": anomaly_event_label(report.anomaly_event)
                        if report.anomaly_event
                        else "UNKNOWN",
                        "summary": report.summary_text,
                    }
                    for report in analyst_reports
                ]
            ),
        }


def age_label(value: object) -> str:
    if value is None or pd.isna(value):
        return "none"
    return f"{float(value):.0f}m"


st.title("Fuck Inside Traders")
st.caption("Dry-run cross-market anomaly radar. Alerts are analysis candidates, not accusations.")

try:
    data = load_dashboard_data()
except Exception as exc:
    st.error(f"Could not load dashboard data: {exc}")
    st.stop()

st.subheader("Live Monitoring State")
monitoring_df = data["monitoring_state"]
if not monitoring_df.empty:
    state = monitoring_df.iloc[0]
    state_cols = st.columns(5)
    state_cols[0].metric(
        "Live Prediction Markets",
        int(state["live_prediction_markets"]),
        "stale"
        if state["prediction_stale"]
        else f"fresh {age_label(state['prediction_age_minutes'])}",
    )
    state_cols[1].metric(
        "Live Asset Snapshots",
        int(state["live_asset_snapshots"]),
        "stale" if state["asset_stale"] else f"fresh {age_label(state['asset_age_minutes'])}",
    )
    state_cols[2].metric(
        "Live Headlines",
        int(state["live_headlines"]),
        "stale" if state["headline_stale"] else f"fresh {age_label(state['headline_age_minutes'])}",
    )
    state_cols[3].metric("Detector", str(state["latest_detector_result"]))
    state_cols[4].metric("Latest Alert", str(state["latest_alert_label"]))
    if (
        int(state["live_prediction_markets"]) > 0
        and int(state["live_asset_snapshots"]) > 0
        and int(state["live_headlines"]) > 0
        and state["latest_detector_result"] == "no_alerts"
    ):
        st.info("Live data collected, no anomaly detected.")

st.subheader("Provider Health")
status_df = data["collector_statuses"]
if not status_df.empty:
    latest_by_type = status_df.sort_values("created_at").groupby("data_type").tail(1)
    status_by_type = {row["data_type"]: row for _, row in latest_by_type.iterrows()}
    provider_cols = st.columns(4)
    prediction_status = status_by_type.get("prediction_market")
    asset_status = status_by_type.get("asset_price")
    news_status = status_by_type.get("headline")
    detector_status = status_by_type.get("detector")
    provider_cols[0].metric(
        "Prediction Market",
        prediction_status["status"] if prediction_status is not None else "unknown",
        prediction_status["provider_kind"] if prediction_status is not None else "unknown",
    )
    provider_cols[1].metric(
        "Assets",
        asset_status["status"] if asset_status is not None else "unknown",
        asset_status["provider_kind"] if asset_status is not None else "unknown",
    )
    provider_cols[2].metric(
        "News",
        news_status["status"] if news_status is not None else "unknown",
        news_status["provider_kind"] if news_status is not None else "unknown",
    )
    provider_cols[3].metric(
        "Detector",
        detector_status["status"] if detector_status is not None else "unknown",
        detector_status["provider_kind"] if detector_status is not None else "unknown",
    )
    if prediction_status is not None and prediction_status["status"] in {
        "fallback",
        "mock_backed",
        "no_live_candidates",
    }:
        st.warning(
            "No live prediction market candidate passed the configured relevance filters. "
            f"{prediction_status['message']}"
        )
    st.dataframe(status_df, width="stretch", hide_index=True)
else:
    st.write("No collector status rows yet.")

st.subheader("Polymarket Discovery")
candidate_df = data["polymarket_candidates"]
if candidate_df.empty:
    st.write("No Polymarket discovery candidates recorded yet.")
else:
    accepted_candidates = candidate_df[candidate_df["accepted"]]
    rejected_candidates = candidate_df[~candidate_df["accepted"]]
    stale_watchlist_df = candidate_df[
        (candidate_df["query"] == "watchlist")
        & (
            candidate_df["closed"]
            | ~candidate_df["active"]
            | candidate_df["reason"].isin(
                ["closed", "inactive", "watchlist_entry_inactive", "watchlist_fetch_failed"]
            )
        )
    ]
    st.caption("Accepted and rejected candidates from watchlist and public-search discovery.")
    discovery_cols = st.columns(3)
    discovery_cols[0].metric("Accepted Live Markets", len(accepted_candidates))
    discovery_cols[1].metric("Rejected Candidates", len(rejected_candidates))
    latest_reason = (
        str(rejected_candidates.iloc[0]["reason"]) if not rejected_candidates.empty else "none"
    )
    discovery_cols[2].metric("Latest Rejection", latest_reason)
    if accepted_candidates.empty:
        st.info("No accepted live Polymarket market in the latest recorded discovery sample.")
    else:
        st.write("Accepted live Polymarket markets")
        st.dataframe(
            accepted_candidates[
                [
                    "created_at",
                    "topic",
                    "query",
                    "score",
                    "provider_kind",
                    "active",
                    "closed",
                    "title",
                    "slug",
                    "external_id",
                    "url",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
    with st.expander("Rejected Polymarket candidates", expanded=False):
        st.dataframe(
            rejected_candidates[
                [
                    "created_at",
                    "topic",
                    "query",
                    "score",
                    "reason",
                    "active",
                    "closed",
                    "title",
                    "slug",
                    "external_id",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
    with st.expander("Stale / Closed / Inactive Watchlist Entries", expanded=False):
        if stale_watchlist_df.empty:
            st.write("No stale, closed, inactive, or unfetched watchlist entries recorded.")
        else:
            st.dataframe(
                stale_watchlist_df[
                    [
                        "created_at",
                        "topic",
                        "score",
                        "reason",
                        "active",
                        "closed",
                        "title",
                        "slug",
                        "external_id",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

st.subheader("Live Market Signal Review")
score_df = data["live_market_scores"]
if score_df.empty:
    st.write("No active live Polymarket markets available for signal review.")
else:
    review_cols = st.columns(4)
    scored_df = score_df[score_df["signal_score"].notna()]
    insufficient_count = int((score_df["status"] == "insufficient_snapshots").sum())
    max_score = float(scored_df["signal_score"].max()) if not scored_df.empty else 0.0
    review_cols[0].metric("Markets Reviewed", len(score_df))
    review_cols[1].metric("Max Signal", f"{max_score:.2f}")
    review_cols[2].metric("Skipped", insufficient_count)
    review_cols[3].metric("Alert Threshold", f"{float(score_df.iloc[0]['alert_threshold']):.2f}")
    st.caption(
        "Current deterministic scores for active live markets. "
        "Rows can be below threshold even when all providers are healthy."
    )
    display_score_df = score_df.sort_values(
        by=["signal_score", "latest_snapshot"],
        ascending=[False, False],
        na_position="last",
    )
    st.dataframe(
        display_score_df[
            [
                "status",
                "signal_score",
                "alert_threshold",
                "odds_score",
                "volume_score",
                "asset_score",
                "headline_score",
                "odds_jump",
                "volume_z",
                "headline_gap_minutes",
                "live_snapshots",
                "total_snapshots",
                "topic",
                "market",
                "why_no_alert",
                "asset_moves",
            ]
        ],
        width="stretch",
        hide_index=True,
    )

events_df = data["events"]
if events_df.empty:
    st.info("No anomaly events yet. Run `make collect-once` and `make detect-once`.")
else:
    review_events_df = events_df[events_df["review_candidate"]]
    demo_events_df = events_df[~events_df["review_candidate"]]

    st.subheader("Live / Review Candidate Events")
    if review_events_df.empty:
        st.info("No live or partial-live anomaly candidates yet.")
    else:
        st.dataframe(review_events_df, width="stretch", hide_index=True)
        latest = review_events_df.iloc[0]
        metric_cols = st.columns(5)
        metric_cols[0].metric("Signal", f"{latest['signal_score']:.2f}")
        metric_cols[1].metric("Provenance", str(latest["provenance"]))
        metric_cols[2].metric("Volume Z", f"{latest['volume_z_score']:.2f}")
        metric_cols[3].metric("Assets", f"{latest['asset_confirmation_score']:.2f}")
        metric_cols[4].metric("Headline Gap", f"{latest['headline_gap_minutes']:.0f}m")

    with st.expander("Demo / Mock / Unknown Events", expanded=False):
        if demo_events_df.empty:
            st.write("No demo/mock events.")
        else:
            st.dataframe(demo_events_df, width="stretch", hide_index=True)

st.subheader("Prediction Market Snapshot Chart")
market_df = data["market_snapshots"]
if not market_df.empty:
    st.line_chart(market_df.set_index("timestamp")[["probability"]])
    st.bar_chart(market_df.set_index("timestamp")[["volume"]])
    st.dataframe(
        market_df[["timestamp", "source", "provider_kind", "probability", "volume"]],
        width="stretch",
        hide_index=True,
    )
else:
    st.write("No prediction market snapshots for the latest event.")

st.subheader("Related Asset Price Chart")
asset_df = data["asset_snapshots"]
if not asset_df.empty:
    pivot = asset_df.pivot(index="timestamp", columns="symbol", values="price")
    st.line_chart(pivot)
    st.dataframe(
        asset_df[["timestamp", "symbol", "source", "provider_kind", "price"]],
        width="stretch",
        hide_index=True,
    )
else:
    st.write("No related asset snapshots for the latest event.")

st.subheader("Headline Sources For Topic")
headline_sources_df = data["headline_source_counts"]
if not headline_sources_df.empty:
    st.dataframe(headline_sources_df, width="stretch", hide_index=True)

st.subheader("Headline Timeline For Topic")
news_df = data["news"]
if not news_df.empty:
    st.dataframe(news_df, width="stretch", hide_index=True)
else:
    st.write("No matching public headlines in the loaded timeline.")

st.subheader("Analyst Reports")
analyst_reports_df = data["analyst_reports"]
if analyst_reports_df.empty:
    st.write("No analyst reports yet.")
else:
    st.dataframe(analyst_reports_df, width="stretch", hide_index=True)

st.subheader("Paper Trades")
paper_df = data["paper_trades"]
if paper_df.empty:
    st.write("No paper trades yet. V1 stores the placeholder model only.")
else:
    st.dataframe(paper_df, width="stretch", hide_index=True)

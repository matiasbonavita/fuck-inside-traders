from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fuck_inside_traders.collectors.news import get_headline_timeline, had_matching_headline_before
from fuck_inside_traders.config import Topic, load_thresholds, load_topics
from fuck_inside_traders.detectors.scoring import (
    combine_signal_score,
    compute_asset_confirmation,
    compute_headline_gap,
    compute_odds_jump,
    compute_volume_spike,
)
from fuck_inside_traders.monitoring import is_stale
from fuck_inside_traders.provenance import (
    ALERT_LIVE,
    alert_label_from_components,
    component_provenance,
)
from fuck_inside_traders.storage.models import (
    AnomalyEvent,
    AssetPriceSnapshot,
    Market,
    NewsItem,
    PredictionMarketSnapshot,
)
from fuck_inside_traders.time_utils import ensure_utc

logger = logging.getLogger(__name__)


class AnomalyDetector:
    def __init__(self, topics: list[Topic] | None = None) -> None:
        self.topics = {topic.name: topic for topic in (topics or load_topics())}
        self.thresholds = load_thresholds()

    def run_once(self, session: Session) -> list[AnomalyEvent]:
        created_events: list[AnomalyEvent] = []
        markets = session.scalars(select(Market).where(Market.active.is_(True))).all()
        for market in markets:
            topic = self.topics.get(market.topic)
            if topic is None:
                logger.warning(
                    "Skipping market with unconfigured topic market_id=%s topic=%s",
                    market.id,
                    market.topic,
                )
                continue

            snapshots = list(
                session.scalars(
                    select(PredictionMarketSnapshot)
                    .where(PredictionMarketSnapshot.market_id == market.id)
                    .order_by(PredictionMarketSnapshot.timestamp.asc())
                    .limit(500)
                )
            )
            if len(snapshots) < 2:
                logger.info("Skipping market with insufficient snapshots market_id=%s", market.id)
                continue

            odds_result = compute_odds_jump(snapshots, self.thresholds.windows_minutes)
            volume_result = compute_volume_spike(snapshots)
            asset_snapshots_by_symbol = self._load_asset_snapshots(session, topic)
            asset_result = compute_asset_confirmation(
                asset_snapshots_by_symbol,
                odds_result.window_minutes or max(self.thresholds.windows_minutes),
            )

            headline_lookback = max(self.thresholds.windows_minutes)
            had_headline = had_matching_headline_before(
                market.topic,
                odds_result.started_at,
                headline_lookback,
                session=session,
            )
            prior_gap = self._minutes_since_latest_prior_headline(
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

            if signal_score < self.thresholds.alert_threshold:
                logger.info(
                    "Signal below alert threshold market_id=%s topic=%s signal_score=%.3f",
                    market.id,
                    market.topic,
                    signal_score,
                )
                continue

            if self._event_exists(session, market.id, odds_result.ended_at):
                logger.info(
                    "Skipping duplicate anomaly event market_id=%s ended_at=%s",
                    market.id,
                    odds_result.ended_at,
                )
                continue

            timeline = get_headline_timeline(
                market.topic,
                odds_result.started_at - timedelta(minutes=headline_lookback),
                odds_result.ended_at + timedelta(minutes=headline_lookback),
                session=session,
            )
            provenance_components = {
                "odds_jump": component_provenance(snapshots, market.source),
                "volume_spike": component_provenance(snapshots, market.source),
                "asset_confirmation": component_provenance(
                    [
                        snapshot
                        for symbol_snapshots in asset_snapshots_by_symbol.values()
                        for snapshot in symbol_snapshots
                    ],
                    "asset_price",
                ),
                "headline_gap": component_provenance(timeline, "headline"),
            }
            alert_label = alert_label_from_components(provenance_components)
            stale_reasons = self._stale_reasons(
                snapshots,
                asset_snapshots_by_symbol,
                timeline,
            )
            if alert_label == ALERT_LIVE and stale_reasons:
                logger.warning(
                    "Skipping LIVE anomaly event because source data is stale "
                    "market_id=%s topic=%s stale_reasons=%s",
                    market.id,
                    market.topic,
                    stale_reasons,
                )
                continue

            event = AnomalyEvent(
                topic=market.topic,
                market=market,
                signal_score=signal_score,
                odds_jump=odds_result.odds_jump,
                volume_z_score=volume_result.z_score,
                asset_confirmation_score=asset_result.score,
                headline_gap_minutes=headline_result.gap_minutes,
                started_at=odds_result.started_at,
                ended_at=odds_result.ended_at,
                explanation_json={
                    "scores": {
                        "odds_jump": odds_result.score,
                        "volume_spike": volume_result.score,
                        "asset_confirmation": asset_result.score,
                        "headline_gap": headline_result.score,
                        "topic_importance": topic.topic_importance,
                    },
                    "odds_window_minutes": odds_result.window_minutes,
                    "asset_moves": asset_result.moves,
                    "had_headline_before": headline_result.had_headline_before,
                    "headline_timeline": [self._news_item_payload(item) for item in timeline[:10]],
                    "market_title": market.title,
                    "provenance": {
                        "alert_label": alert_label,
                        "components": provenance_components,
                        "stale_reasons": stale_reasons,
                    },
                },
            )
            session.add(event)
            session.flush()
            created_events.append(event)
            logger.info(
                "Created anomaly event id=%s topic=%s market_id=%s signal_score=%.3f label=%s",
                event.id,
                event.topic,
                market.id,
                signal_score,
                alert_label,
            )
        return created_events

    def _stale_reasons(
        self,
        prediction_snapshots: list[PredictionMarketSnapshot],
        asset_snapshots_by_symbol: dict[str, list[AssetPriceSnapshot]],
        headline_timeline: list[NewsItem],
    ) -> list[str]:
        now = datetime.now(UTC)
        reasons = []

        latest_prediction = max(
            (ensure_utc(snapshot.timestamp) for snapshot in prediction_snapshots),
            default=None,
        )
        if is_stale(latest_prediction, self.thresholds.prediction_market_fresh_minutes, now):
            reasons.append("prediction_market")

        latest_asset = max(
            (
                ensure_utc(snapshot.timestamp)
                for symbol_snapshots in asset_snapshots_by_symbol.values()
                for snapshot in symbol_snapshots
            ),
            default=None,
        )
        if is_stale(latest_asset, self.thresholds.asset_price_fresh_minutes, now):
            reasons.append("asset_price")

        latest_headline = max(
            (ensure_utc(item.published_at) for item in headline_timeline),
            default=None,
        )
        if is_stale(latest_headline, self.thresholds.headline_fresh_minutes, now):
            reasons.append("headline")

        return reasons

    def _load_asset_snapshots(
        self,
        session: Session,
        topic: Topic,
    ) -> dict[str, list[AssetPriceSnapshot]]:
        snapshots_by_symbol: dict[str, list[AssetPriceSnapshot]] = {}
        for symbol in topic.assets:
            snapshots_by_symbol[symbol] = list(
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
        return snapshots_by_symbol

    def _minutes_since_latest_prior_headline(
        self,
        session: Session,
        topic: str,
        before_timestamp: Any,
        lookback_minutes: int,
    ) -> float | None:
        before_timestamp = ensure_utc(before_timestamp)
        latest = session.scalar(
            select(NewsItem)
            .where(
                NewsItem.topic == topic,
                NewsItem.published_at >= before_timestamp - timedelta(minutes=lookback_minutes),
                NewsItem.published_at <= before_timestamp,
            )
            .order_by(desc(NewsItem.published_at))
            .limit(1)
        )
        if latest is None:
            return None
        return (before_timestamp - ensure_utc(latest.published_at)).total_seconds() / 60.0

    def _event_exists(self, session: Session, market_id: int, ended_at: Any) -> bool:
        return (
            session.scalar(
                select(AnomalyEvent.id)
                .where(AnomalyEvent.market_id == market_id, AnomalyEvent.ended_at == ended_at)
                .limit(1)
            )
            is not None
        )

    def _news_item_payload(self, item: NewsItem) -> dict[str, Any]:
        return {
            "source": item.source,
            "title": item.title,
            "url": item.url,
            "published_at": ensure_utc(item.published_at).isoformat(),
        }

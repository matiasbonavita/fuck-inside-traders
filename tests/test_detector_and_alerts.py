from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from fuck_inside_traders.alerts.telegram import format_alert
from fuck_inside_traders.config import Topic
from fuck_inside_traders.detectors.engine import AnomalyDetector
from fuck_inside_traders.provenance import ALERT_LIVE, ALERT_MOCK_BACKED, LIVE, SYNTHETIC
from fuck_inside_traders.storage.models import (
    AnomalyEvent,
    AssetPriceSnapshot,
    Market,
    NewsItem,
    PredictionMarketSnapshot,
)


def test_detector_creates_anomaly_event_from_fake_snapshots(db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    topic = Topic(
        name="iran_oil",
        keywords=["iran", "oil"],
        assets=["USO"],
        topic_importance=1.0,
    )
    market = Market(
        source="polymarket_mock",
        external_id="mock-iran-oil",
        title="Will iran oil exports be disrupted?",
        topic=topic.name,
        url="https://example.local/market",
        active=True,
    )
    db_session.add(market)
    db_session.flush()
    db_session.add_all(
        [
            PredictionMarketSnapshot(
                market_id=market.id,
                probability=0.35,
                volume=100.0,
                liquidity=1000.0,
                bid=0.34,
                ask=0.36,
                timestamp=now - timedelta(minutes=30),
                source="fixture_synthetic_baseline",
                provider_kind=SYNTHETIC,
            ),
            PredictionMarketSnapshot(
                market_id=market.id,
                probability=0.65,
                volume=1000.0,
                liquidity=2000.0,
                bid=0.64,
                ask=0.66,
                timestamp=now,
                source="fixture_live",
                provider_kind=LIVE,
            ),
            AssetPriceSnapshot(
                symbol="USO",
                topic=topic.name,
                price=100.0,
                volume=100_000.0,
                timestamp=now - timedelta(minutes=30),
                source="fixture_synthetic_baseline",
                provider_kind=SYNTHETIC,
            ),
            AssetPriceSnapshot(
                symbol="USO",
                topic=topic.name,
                price=103.0,
                volume=200_000.0,
                timestamp=now,
                source="fixture_live",
                provider_kind=LIVE,
            ),
            NewsItem(
                source="mock_news",
                title="Public oil headline after move",
                url="https://example.local/news-after",
                published_at=now + timedelta(minutes=5),
                topic=topic.name,
                raw_payload_json={"after": True},
                provider_kind=LIVE,
            ),
        ]
    )
    db_session.commit()

    events = AnomalyDetector(topics=[topic]).run_once(db_session)
    db_session.commit()

    assert len(events) == 1
    event = db_session.scalar(select(AnomalyEvent))
    assert event is not None
    assert event.signal_score >= 0.8
    assert event.explanation_json["had_headline_before"] is False
    assert event.explanation_json["provenance"]["alert_label"] == ALERT_MOCK_BACKED

    duplicate_events = AnomalyDetector(topics=[topic]).run_once(db_session)
    assert duplicate_events == []


def test_detector_can_create_live_labeled_event(db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    topic = Topic(
        name="iran_oil",
        keywords=["iran", "oil"],
        assets=["USO"],
        topic_importance=1.0,
    )
    market = Market(
        source="polymarket",
        external_id="live-iran-oil",
        title="Will Iran oil exports be disrupted?",
        topic=topic.name,
        url="https://example.local/live-market",
        active=True,
    )
    db_session.add(market)
    db_session.flush()
    db_session.add_all(
        [
            PredictionMarketSnapshot(
                market_id=market.id,
                probability=0.35,
                volume=100.0,
                liquidity=1000.0,
                bid=0.34,
                ask=0.36,
                timestamp=now - timedelta(minutes=30),
                source="polymarket",
                provider_kind=LIVE,
            ),
            PredictionMarketSnapshot(
                market_id=market.id,
                probability=0.65,
                volume=1000.0,
                liquidity=2000.0,
                bid=0.64,
                ask=0.66,
                timestamp=now,
                source="polymarket",
                provider_kind=LIVE,
            ),
            AssetPriceSnapshot(
                symbol="USO",
                topic=topic.name,
                price=100.0,
                volume=100_000.0,
                timestamp=now - timedelta(minutes=30),
                source="yfinance",
                provider_kind=LIVE,
            ),
            AssetPriceSnapshot(
                symbol="USO",
                topic=topic.name,
                price=103.0,
                volume=200_000.0,
                timestamp=now,
                source="yfinance",
                provider_kind=LIVE,
            ),
            NewsItem(
                source="rss:fixture",
                title="Public oil headline after move",
                url="https://example.local/live-news-after",
                published_at=now + timedelta(minutes=5),
                topic=topic.name,
                raw_payload_json={"after": True},
                provider_kind=LIVE,
            ),
        ]
    )
    db_session.commit()

    events = AnomalyDetector(topics=[topic]).run_once(db_session)

    assert len(events) == 1
    assert events[0].explanation_json["provenance"]["alert_label"] == ALERT_LIVE


def test_detector_skips_live_alert_when_core_data_is_stale(db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    started_at = now - timedelta(minutes=120)
    ended_at = now - timedelta(minutes=90)
    topic = Topic(
        name="iran_oil",
        keywords=["iran", "oil"],
        assets=["USO"],
        topic_importance=1.0,
    )
    market = Market(
        source="polymarket",
        external_id="stale-live-iran-oil",
        title="Will Iran oil exports be disrupted?",
        topic=topic.name,
        url="https://example.local/stale-live-market",
        active=True,
    )
    db_session.add(market)
    db_session.flush()
    db_session.add_all(
        [
            PredictionMarketSnapshot(
                market_id=market.id,
                probability=0.35,
                volume=100.0,
                liquidity=1000.0,
                bid=0.34,
                ask=0.36,
                timestamp=started_at,
                source="polymarket",
                provider_kind=LIVE,
            ),
            PredictionMarketSnapshot(
                market_id=market.id,
                probability=0.65,
                volume=1000.0,
                liquidity=2000.0,
                bid=0.64,
                ask=0.66,
                timestamp=ended_at,
                source="polymarket",
                provider_kind=LIVE,
            ),
            AssetPriceSnapshot(
                symbol="USO",
                topic=topic.name,
                price=100.0,
                volume=100_000.0,
                timestamp=started_at,
                source="yfinance",
                provider_kind=LIVE,
            ),
            AssetPriceSnapshot(
                symbol="USO",
                topic=topic.name,
                price=103.0,
                volume=200_000.0,
                timestamp=ended_at,
                source="yfinance",
                provider_kind=LIVE,
            ),
            NewsItem(
                source="rss:fixture",
                title="Public oil headline after stale move",
                url="https://example.local/stale-live-news-after",
                published_at=ended_at + timedelta(minutes=5),
                topic=topic.name,
                raw_payload_json={"after": True},
                provider_kind=LIVE,
            ),
        ]
    )
    db_session.commit()

    events = AnomalyDetector(topics=[topic]).run_once(db_session)

    assert events == []
    assert db_session.scalar(select(AnomalyEvent)) is None


def test_alert_format_includes_required_fields() -> None:
    now = datetime.now(UTC)
    market = Market(
        id=1,
        source="polymarket_mock",
        external_id="mock",
        title="Will iran oil exports be disrupted?",
        topic="iran_oil",
        url="https://example.local",
        active=True,
    )
    event = AnomalyEvent(
        id=1,
        topic="iran_oil",
        market_id=1,
        signal_score=0.92,
        odds_jump=0.22,
        volume_z_score=3.0,
        asset_confirmation_score=0.9,
        headline_gap_minutes=30.0,
        started_at=now - timedelta(minutes=30),
        ended_at=now,
        explanation_json={
            "market_title": market.title,
            "scores": {
                "odds_jump": 1.0,
                "volume_spike": 0.99,
                "asset_confirmation": 0.9,
                "headline_gap": 1.0,
            },
            "asset_moves": {"USO": 0.03},
            "headline_timeline": [],
            "had_headline_before": False,
            "provenance": {
                "alert_label": ALERT_MOCK_BACKED,
                "components": {
                    "odds_jump": {
                        "provider_kinds": [SYNTHETIC, LIVE],
                        "sources": ["fixture_synthetic_baseline", "fixture_live"],
                    }
                },
            },
        },
    )

    message = format_alert(event, market)

    assert "DRY-RUN anomaly alert" in message
    assert "Topic: iran_oil" in message
    assert "Odds jump" in message
    assert "Volume z-score" in message
    assert "Headline gap" in message
    assert "Provenance label: MOCK-BACKED" in message
    assert "Demo-only notice" in message
    assert "not an accusation" in message

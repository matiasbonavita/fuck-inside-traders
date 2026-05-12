from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from fuck_inside_traders.storage.models import (
    AnalystReport,
    AnomalyEvent,
    AssetPriceSnapshot,
    Market,
    NewsItem,
    PaperTrade,
    PredictionMarketSnapshot,
)


def test_model_creation_and_basic_persistence(db_session) -> None:
    now = datetime.now(UTC)
    market = Market(
        source="polymarket_mock",
        external_id="mock-1",
        title="Will oil move?",
        topic="iran_oil",
        url="https://example.local",
        active=True,
    )
    db_session.add(market)
    db_session.flush()

    db_session.add(
        PredictionMarketSnapshot(
            market_id=market.id,
            probability=0.55,
            volume=1000.0,
            liquidity=500.0,
            bid=0.54,
            ask=0.56,
            timestamp=now,
        )
    )
    db_session.add(
        AssetPriceSnapshot(
            symbol="USO",
            topic="iran_oil",
            price=80.0,
            volume=100_000.0,
            timestamp=now,
        )
    )
    db_session.add(
        NewsItem(
            source="mock_news",
            title="Oil headline",
            url="https://example.local/news",
            published_at=now,
            topic="iran_oil",
            raw_payload_json={"ok": True},
        )
    )
    event = AnomalyEvent(
        topic="iran_oil",
        market_id=market.id,
        signal_score=0.9,
        odds_jump=0.2,
        volume_z_score=3.0,
        asset_confirmation_score=0.8,
        headline_gap_minutes=30.0,
        started_at=now,
        ended_at=now,
        explanation_json={"market_title": market.title},
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        PaperTrade(
            anomaly_event_id=event.id,
            symbol="USO",
            direction="watch",
            status="planned",
        )
    )
    db_session.add(
        AnalystReport(
            anomaly_event_id=event.id,
            backend="deterministic",
            status="success",
            report_json={"ok": True},
            summary_text="Dry-run public-data anomaly report for manual review, not an accusation.",
        )
    )
    db_session.commit()

    assert db_session.scalar(select(Market).where(Market.external_id == "mock-1")) is not None
    assert (
        db_session.scalar(select(AnomalyEvent).where(AnomalyEvent.topic == "iran_oil"))
        is not None
    )
    assert db_session.scalar(select(PaperTrade).where(PaperTrade.symbol == "USO")) is not None
    assert (
        db_session.scalar(select(AnalystReport).where(AnalystReport.backend == "deterministic"))
        is not None
    )

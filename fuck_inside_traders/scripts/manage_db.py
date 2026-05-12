from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from fuck_inside_traders.config import load_topics
from fuck_inside_traders.detectors.engine import AnomalyDetector
from fuck_inside_traders.logging_config import configure_logging
from fuck_inside_traders.provenance import (
    ALERT_MOCK_BACKED,
    ALERT_UNKNOWN,
    MOCK,
    SYNTHETIC,
    UNKNOWN,
    is_demo_or_mock_event,
)
from fuck_inside_traders.storage.database import engine, init_db, session_scope
from fuck_inside_traders.storage.models import (
    AnomalyEvent,
    AssetPriceSnapshot,
    Base,
    CollectorStatus,
    Market,
    NewsItem,
    PredictionMarketSnapshot,
)

logger = logging.getLogger(__name__)


def db_reset() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()
    logger.info("Local database reset complete")


def backfill_provenance() -> int:
    updated = 0
    with session_scope() as session:
        events = session.scalars(select(AnomalyEvent)).all()
        for event in events:
            explanation = dict(event.explanation_json or {})
            if explanation.get("provenance", {}).get("alert_label"):
                continue

            market = event.market
            market_source = market.source if market else UNKNOWN
            label = ALERT_MOCK_BACKED if "mock" in market_source.lower() else ALERT_UNKNOWN
            provider_kind = MOCK if label == ALERT_MOCK_BACKED else UNKNOWN
            explanation["provenance"] = {
                "alert_label": label,
                "components": {
                    "legacy_event": {
                        "provider_kinds": [provider_kind],
                        "sources": [market_source or "legacy_event"],
                    }
                },
            }
            event.explanation_json = explanation
            updated += 1
    logger.info("Backfilled anomaly event provenance updated=%s", updated)
    return updated


def clean_demo() -> dict[str, int]:
    counts = {
        "events": 0,
        "prediction_snapshots": 0,
        "asset_snapshots": 0,
        "news_items": 0,
        "markets": 0,
    }
    demo_kinds = {MOCK, SYNTHETIC, UNKNOWN}
    with session_scope() as session:
        for event in session.scalars(select(AnomalyEvent)).all():
            market_source = event.market.source if event.market else ""
            if is_demo_or_mock_event(event) or "mock" in market_source.lower():
                session.delete(event)
                counts["events"] += 1
        session.flush()

        for snapshot in session.scalars(
            select(PredictionMarketSnapshot).where(
                PredictionMarketSnapshot.provider_kind.in_(demo_kinds)
            )
        ):
            session.delete(snapshot)
            counts["prediction_snapshots"] += 1

        for snapshot in session.scalars(
            select(AssetPriceSnapshot).where(AssetPriceSnapshot.provider_kind.in_(demo_kinds))
        ):
            session.delete(snapshot)
            counts["asset_snapshots"] += 1

        for item in session.scalars(select(NewsItem).where(NewsItem.provider_kind.in_({MOCK}))):
            session.delete(item)
            counts["news_items"] += 1

        for market in session.scalars(select(Market).where(Market.source.ilike("%mock%"))):
            session.delete(market)
            counts["markets"] += 1

        session.add(
            CollectorStatus(
                provider="local_db",
                data_type="maintenance",
                status="clean_demo",
                provider_kind="system",
                message=f"Removed demo/mock rows counts={counts}",
            )
        )
    logger.info("Cleaned demo/mock data counts=%s", counts)
    return counts


def seed_demo() -> int:
    clean_demo()
    topics = load_topics()
    if not topics:
        msg = "Cannot seed demo data without at least one configured topic"
        raise RuntimeError(msg)
    topic = topics[0]
    now = datetime.now(UTC).replace(microsecond=0)

    with session_scope() as session:
        market = Market(
            source="polymarket_mock_demo",
            external_id=f"demo-{topic.name}-market",
            title=f"DEMO: Will {topic.name.replace('_', ' ')} disrupt energy markets?",
            topic=topic.name,
            url="https://example.local/demo/polymarket",
            active=True,
        )
        session.add(market)
        session.flush()
        session.add_all(
            [
                PredictionMarketSnapshot(
                    market_id=market.id,
                    probability=0.38,
                    volume=1_000.0,
                    liquidity=10_000.0,
                    bid=0.37,
                    ask=0.39,
                    timestamp=now - timedelta(minutes=30),
                    source="polymarket_mock_demo_synthetic_baseline",
                    provider_kind=SYNTHETIC,
                ),
                PredictionMarketSnapshot(
                    market_id=market.id,
                    probability=0.64,
                    volume=42_000.0,
                    liquidity=18_000.0,
                    bid=0.63,
                    ask=0.65,
                    timestamp=now,
                    source="polymarket_mock_demo",
                    provider_kind=MOCK,
                ),
            ]
        )
        for symbol, price in {"USO": 82.45, "XLE": 97.10, "BNO": 35.80}.items():
            session.add_all(
                [
                    AssetPriceSnapshot(
                        symbol=symbol,
                        topic=topic.name,
                        price=price * 0.975,
                        volume=500_000.0,
                        timestamp=now - timedelta(minutes=30),
                        source="yfinance_mock_demo_synthetic_baseline",
                        provider_kind=SYNTHETIC,
                    ),
                    AssetPriceSnapshot(
                        symbol=symbol,
                        topic=topic.name,
                        price=price,
                        volume=1_000_000.0,
                        timestamp=now,
                        source="yfinance_mock_demo",
                        provider_kind=MOCK,
                    ),
                ]
            )
        session.add(
            NewsItem(
                source="mock_news_demo",
                provider_kind=MOCK,
                title=f"DEMO: Public headline after {topic.name.replace('_', ' ')} move",
                url=f"https://example.local/demo/news/{topic.name}",
                published_at=now + timedelta(minutes=5),
                topic=topic.name,
                raw_payload_json={"demo": True},
            )
        )
        session.flush()
        events = AnomalyDetector(topics=[topic]).run_once(session)
        session.add(
            CollectorStatus(
                provider="local_db",
                data_type="maintenance",
                status="seed_demo",
                provider_kind="system",
                message=f"Seeded demo data events_created={len(events)}",
            )
        )
    logger.info("Seeded deterministic demo data events_created=%s", len(events))
    return len(events)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local database maintenance workflows.")
    parser.add_argument(
        "command",
        choices=["db-reset", "seed-demo", "clean-demo", "backfill-provenance"],
    )
    args = parser.parse_args()

    configure_logging()
    init_db()

    if args.command == "db-reset":
        db_reset()
    elif args.command == "seed-demo":
        seed_demo()
    elif args.command == "clean-demo":
        clean_demo()
    elif args.command == "backfill-provenance":
        backfill_provenance()


if __name__ == "__main__":
    main()

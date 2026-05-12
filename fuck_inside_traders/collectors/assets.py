from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from fuck_inside_traders.config import Topic, load_topics
from fuck_inside_traders.provenance import LIVE, MOCK, SYNTHETIC
from fuck_inside_traders.storage.models import AssetPriceSnapshot, CollectorStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetPriceRecord:
    source: str
    provider_kind: str
    symbol: str
    topic: str
    price: float
    volume: float | None
    timestamp: datetime


class AssetPriceProvider(Protocol):
    def fetch_latest(self, topics: list[Topic]) -> list[AssetPriceRecord]:
        pass


class YFinanceAssetPriceProvider:
    def fetch_latest(self, topics: list[Topic]) -> list[AssetPriceRecord]:
        try:
            import yfinance as yf
        except Exception as exc:
            logger.exception("Failed to import yfinance error=%s", exc)
            return []

        now = datetime.now(UTC).replace(microsecond=0)
        records: list[AssetPriceRecord] = []
        for topic in topics:
            for symbol in topic.assets:
                try:
                    ticker = yf.Ticker(symbol)
                    history = ticker.history(period="1d", interval="1m")
                except Exception as exc:
                    logger.exception(
                        "Asset price fetch failed provider=yfinance symbol=%s topic=%s error=%s",
                        symbol,
                        topic.name,
                        exc,
                    )
                    continue

                if history.empty:
                    logger.warning(
                        "No yfinance history rows symbol=%s topic=%s",
                        symbol,
                        topic.name,
                    )
                    continue

                latest = history.iloc[-1]
                price = float(latest.get("Close"))
                volume = latest.get("Volume")
                records.append(
                    AssetPriceRecord(
                        symbol=symbol,
                        source="yfinance",
                        provider_kind=LIVE,
                        topic=topic.name,
                        price=price,
                        volume=float(volume) if volume is not None else None,
                        timestamp=now,
                    )
                )
        return records


class FallbackAssetPriceProvider:
    def fetch_latest(self, topics: list[Topic]) -> list[AssetPriceRecord]:
        now = datetime.now(UTC).replace(microsecond=0)
        prices = {
            "USO": 82.45,
            "XLE": 97.10,
            "BNO": 35.80,
            "GLD": 226.40,
            "SPY": 524.25,
        }
        records: list[AssetPriceRecord] = []
        for topic in topics:
            for symbol in topic.assets:
                records.append(
                    AssetPriceRecord(
                        symbol=symbol,
                        source="yfinance_mock",
                        provider_kind=MOCK,
                        topic=topic.name,
                        price=prices.get(symbol, 100.0),
                        volume=1_000_000.0,
                        timestamp=now,
                    )
                )
        return records


class AssetPriceCollector:
    def __init__(
        self,
        provider: AssetPriceProvider | None = None,
        fallback_provider: AssetPriceProvider | None = None,
    ) -> None:
        self.provider = provider or YFinanceAssetPriceProvider()
        self.fallback_provider = fallback_provider or FallbackAssetPriceProvider()

    def collect(self, session: Session, topics: list[Topic] | None = None) -> int:
        topics = topics or load_topics()
        records = self.provider.fetch_latest(topics)
        used_fallback = False
        if not records:
            logger.warning("No live asset price records found; using fallback provider")
            records = self.fallback_provider.fetch_latest(topics)
            used_fallback = True
        if any(record.provider_kind != LIVE for record in records):
            used_fallback = True

        for record in records:
            self._seed_first_run_baseline(session, record)
            session.add(
                AssetPriceSnapshot(
                    symbol=record.symbol,
                    topic=record.topic,
                    price=record.price,
                    volume=record.volume,
                    timestamp=record.timestamp,
                    source=record.source,
                    provider_kind=record.provider_kind,
                )
            )
            session.flush()
        session.add(
            CollectorStatus(
                provider="yfinance",
                data_type="asset_price",
                status="fallback" if used_fallback else "ok",
                provider_kind=MOCK if used_fallback else LIVE,
                message=f"Collected asset price snapshots count={len(records)}",
            )
        )
        logger.info("Collected asset price snapshots count=%s", len(records))
        return len(records)

    def _seed_first_run_baseline(self, session: Session, record: AssetPriceRecord) -> None:
        existing = session.scalar(
            select(AssetPriceSnapshot.id)
            .where(
                AssetPriceSnapshot.symbol == record.symbol,
                AssetPriceSnapshot.topic == record.topic,
            )
            .limit(1)
        )
        if existing is not None:
            return

        baseline_multiplier = 0.975 if record.symbol in {"USO", "XLE", "BNO"} else 0.992
        session.add(
            AssetPriceSnapshot(
                symbol=record.symbol,
                topic=record.topic,
                price=record.price * baseline_multiplier,
                volume=max(1.0, (record.volume or 1000.0) * 0.5),
                timestamp=record.timestamp - timedelta(minutes=30),
                source=f"{record.source}_synthetic_baseline",
                provider_kind=SYNTHETIC,
            )
        )
        session.flush()

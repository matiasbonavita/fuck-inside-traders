from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (
        Index("ix_markets_source_external_id", "source", "external_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    snapshots: Mapped[list[PredictionMarketSnapshot]] = relationship(
        back_populates="market",
        cascade="all, delete-orphan",
    )
    anomaly_events: Mapped[list[AnomalyEvent]] = relationship(back_populates="market")


class PredictionMarketSnapshot(Base):
    __tablename__ = "prediction_market_snapshots"
    __table_args__ = (
        Index("ix_prediction_snapshots_market_timestamp", "market_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    probability: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    provider_kind: Mapped[str] = mapped_column(String(32), default="unknown")

    market: Mapped[Market] = relationship(back_populates="snapshots")


class PolymarketDiscoveryCandidate(Base):
    __tablename__ = "polymarket_discovery_candidates"
    __table_args__ = (
        Index("ix_polymarket_candidates_topic_created_at", "topic", "created_at"),
        Index("ix_polymarket_candidates_accepted_created_at", "accepted", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    query: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="polymarket")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted: Mapped[bool] = mapped_column(Boolean, index=True, default=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_kind: Mapped[str] = mapped_column(String(32), default="live")
    raw_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssetPriceSnapshot(Base):
    __tablename__ = "asset_price_snapshots"
    __table_args__ = (Index("ix_asset_snapshots_symbol_timestamp", "symbol", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    price: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    provider_kind: Mapped[str] = mapped_column(String(32), default="unknown")


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (
        Index("ix_news_topic_published_at", "topic", "published_at"),
        Index("ix_news_source_url", "source", "url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    raw_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    provider_kind: Mapped[str] = mapped_column(String(32), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"
    __table_args__ = (
        Index("ix_anomaly_topic_created_at", "topic", "created_at"),
        Index("ix_anomaly_market_started_at", "market_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    market_id: Mapped[int | None] = mapped_column(
        ForeignKey("markets.id"),
        nullable=True,
        index=True,
    )
    signal_score: Mapped[float] = mapped_column(Float)
    odds_jump: Mapped[float] = mapped_column(Float)
    volume_z_score: Mapped[float] = mapped_column(Float)
    asset_confirmation_score: Mapped[float] = mapped_column(Float)
    headline_gap_minutes: Mapped[float] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    explanation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
    )

    market: Mapped[Market | None] = relationship(back_populates="anomaly_events")
    paper_trades: Mapped[list[PaperTrade]] = relationship(
        back_populates="anomaly_event",
        cascade="all, delete-orphan",
    )
    analyst_reports: Mapped[list[AnalystReport]] = relationship(
        back_populates="anomaly_event",
        cascade="all, delete-orphan",
    )


class AnalystReport(Base):
    __tablename__ = "analyst_reports"
    __table_args__ = (
        Index("ix_analyst_reports_event_backend", "anomaly_event_id", "backend", unique=True),
        Index("ix_analyst_reports_backend_created_at", "backend", "created_at"),
        Index("ix_analyst_reports_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anomaly_event_id: Mapped[int] = mapped_column(ForeignKey("anomaly_events.id"), index=True)
    backend: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    anomaly_event: Mapped[AnomalyEvent] = relationship(back_populates="analyst_reports")


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    __table_args__ = (Index("ix_paper_trade_status_created_at", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anomaly_event_id: Mapped[int] = mapped_column(ForeignKey("anomaly_events.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="planned")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    anomaly_event: Mapped[AnomalyEvent] = relationship(back_populates="paper_trades")


class CollectorStatus(Base):
    __tablename__ = "collector_statuses"
    __table_args__ = (
        Index("ix_collector_status_provider_created_at", "provider", "created_at"),
        Index("ix_collector_status_data_type_created_at", "data_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(128), index=True)
    data_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    provider_kind: Mapped[str] = mapped_column(String(32), default="unknown")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

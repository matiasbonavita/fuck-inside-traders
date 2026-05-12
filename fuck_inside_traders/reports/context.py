from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from fuck_inside_traders.provenance import anomaly_event_label
from fuck_inside_traders.reports.schemas import (
    AnalystContext,
    AssetSnapshotContext,
    EventContext,
    HeadlineContext,
    MarketContext,
    PredictionSnapshotContext,
    ProvenanceComponentContext,
    ProvenanceContext,
    ProviderStatusContext,
    ScoreContext,
)
from fuck_inside_traders.storage.models import (
    AnomalyEvent,
    AssetPriceSnapshot,
    CollectorStatus,
    NewsItem,
    PredictionMarketSnapshot,
)
from fuck_inside_traders.time_utils import ensure_utc

DEFAULT_CONTEXT_WINDOW_MINUTES = 60

SAFETY_NOTES = [
    "Dry-run public-data anomaly review only.",
    "This report must not identify suspects or accuse anyone of insider trading.",
    "Hermes, if enabled, may summarize context but must not decide whether an anomaly exists.",
    "No trading execution, brokerage connection, or position sizing is allowed.",
]


def assemble_analyst_context(
    session: Session,
    event: AnomalyEvent,
    window_minutes: int = DEFAULT_CONTEXT_WINDOW_MINUTES,
) -> AnalystContext:
    market = event.market
    explanation = event.explanation_json or {}
    scores = explanation.get("scores") or {}
    provenance = explanation.get("provenance") or {}
    started_at = ensure_utc(event.started_at)
    ended_at = ensure_utc(event.ended_at)
    window_start = started_at - timedelta(minutes=window_minutes)
    window_end = ended_at + timedelta(minutes=window_minutes)
    status_center = ensure_utc(event.created_at or ended_at)
    status_start = status_center - timedelta(minutes=window_minutes)
    status_end = status_center + timedelta(minutes=window_minutes)

    prediction_snapshots: list[PredictionMarketSnapshot] = []
    if event.market_id is not None:
        prediction_snapshots = list(
            session.scalars(
                select(PredictionMarketSnapshot)
                .where(
                    PredictionMarketSnapshot.market_id == event.market_id,
                    PredictionMarketSnapshot.timestamp >= window_start,
                    PredictionMarketSnapshot.timestamp <= window_end,
                )
                .order_by(PredictionMarketSnapshot.timestamp.asc())
            )
        )

    asset_snapshots = list(
        session.scalars(
            select(AssetPriceSnapshot)
            .where(
                AssetPriceSnapshot.topic == event.topic,
                AssetPriceSnapshot.timestamp >= window_start,
                AssetPriceSnapshot.timestamp <= window_end,
            )
            .order_by(AssetPriceSnapshot.symbol.asc(), AssetPriceSnapshot.timestamp.asc())
        )
    )
    headline_timeline = list(
        session.scalars(
            select(NewsItem)
            .where(
                NewsItem.topic == event.topic,
                NewsItem.published_at >= window_start,
                NewsItem.published_at <= window_end,
            )
            .order_by(NewsItem.published_at.asc())
            .limit(50)
        )
    )
    provider_statuses = list(
        session.scalars(
            select(CollectorStatus)
            .where(
                CollectorStatus.created_at >= status_start,
                CollectorStatus.created_at <= status_end,
            )
            .order_by(CollectorStatus.created_at.asc())
            .limit(50)
        )
    )

    components = {
        name: ProvenanceComponentContext(
            provider_kinds=list(component.get("provider_kinds") or []),
            sources=list(component.get("sources") or []),
        )
        for name, component in (provenance.get("components") or {}).items()
        if isinstance(component, dict)
    }

    return AnalystContext(
        event=EventContext(
            id=event.id,
            topic=event.topic,
            signal_score=event.signal_score,
            odds_jump=event.odds_jump,
            volume_z_score=event.volume_z_score,
            asset_confirmation_score=event.asset_confirmation_score,
            headline_gap_minutes=event.headline_gap_minutes,
            started_at=started_at,
            ended_at=ended_at,
            created_at=ensure_utc(event.created_at) if event.created_at else None,
        ),
        market=MarketContext(
            id=market.id,
            source=market.source,
            external_id=market.external_id,
            title=market.title,
            topic=market.topic,
            url=market.url,
            active=market.active,
        )
        if market
        else None,
        scores=ScoreContext(
            odds_jump=float(scores.get("odds_jump", 0.0)),
            volume_spike=float(scores.get("volume_spike", 0.0)),
            asset_confirmation=float(scores.get("asset_confirmation", 0.0)),
            headline_gap=float(scores.get("headline_gap", 0.0)),
            topic_importance=float(scores.get("topic_importance", 0.0)),
        ),
        prediction_snapshots=[
            PredictionSnapshotContext(
                timestamp=ensure_utc(snapshot.timestamp),
                probability=snapshot.probability,
                volume=snapshot.volume,
                liquidity=snapshot.liquidity,
                bid=snapshot.bid,
                ask=snapshot.ask,
                source=snapshot.source,
                provider_kind=snapshot.provider_kind,
            )
            for snapshot in prediction_snapshots
        ],
        asset_snapshots=[
            AssetSnapshotContext(
                timestamp=ensure_utc(snapshot.timestamp),
                symbol=snapshot.symbol,
                topic=snapshot.topic,
                price=snapshot.price,
                volume=snapshot.volume,
                source=snapshot.source,
                provider_kind=snapshot.provider_kind,
            )
            for snapshot in asset_snapshots
        ],
        headline_timeline=[
            HeadlineContext(
                published_at=ensure_utc(item.published_at),
                source=item.source,
                provider_kind=item.provider_kind,
                title=item.title,
                url=item.url,
            )
            for item in headline_timeline
        ],
        provider_statuses=[
            ProviderStatusContext(
                created_at=ensure_utc(status.created_at),
                provider=status.provider,
                data_type=status.data_type,
                status=status.status,
                provider_kind=status.provider_kind,
                message=status.message,
            )
            for status in provider_statuses
        ],
        provenance=ProvenanceContext(
            alert_label=str(provenance.get("alert_label") or anomaly_event_label(event)),
            components=components,
            stale_reasons=list(provenance.get("stale_reasons") or []),
        ),
        safety_notes=SAFETY_NOTES,
    )

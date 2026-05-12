from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from fuck_inside_traders.reports.analyst import (
    DeterministicAnalystBackend,
    HermesAnalystBackend,
)
from fuck_inside_traders.reports.context import assemble_analyst_context
from fuck_inside_traders.scripts.run_analyst import run_analyst_once
from fuck_inside_traders.storage.models import (
    AnalystReport,
    AnomalyEvent,
    AssetPriceSnapshot,
    CollectorStatus,
    Market,
    NewsItem,
    PredictionMarketSnapshot,
)


def _create_event_fixture(db_session) -> AnomalyEvent:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    market = Market(
        source="polymarket",
        external_id="pm-analyst-1",
        title="Will oil shipping through Hormuz normalize?",
        topic="iran_oil",
        url="https://example.local/market",
        active=True,
    )
    db_session.add(market)
    db_session.flush()
    db_session.add_all(
        [
            PredictionMarketSnapshot(
                market_id=market.id,
                probability=0.30,
                volume=1000.0,
                liquidity=500.0,
                bid=0.29,
                ask=0.31,
                timestamp=now - timedelta(minutes=30),
                source="polymarket",
                provider_kind="live",
            ),
            PredictionMarketSnapshot(
                market_id=market.id,
                probability=0.55,
                volume=5000.0,
                liquidity=750.0,
                bid=0.54,
                ask=0.56,
                timestamp=now,
                source="polymarket",
                provider_kind="live",
            ),
            AssetPriceSnapshot(
                symbol="USO",
                topic="iran_oil",
                price=80.0,
                volume=1_000_000.0,
                timestamp=now - timedelta(minutes=30),
                source="yfinance",
                provider_kind="live",
            ),
            AssetPriceSnapshot(
                symbol="USO",
                topic="iran_oil",
                price=82.0,
                volume=1_100_000.0,
                timestamp=now,
                source="yfinance",
                provider_kind="live",
            ),
            NewsItem(
                source="rss:test",
                title="Public oil shipping headline",
                url="https://example.local/news",
                published_at=now - timedelta(minutes=5),
                topic="iran_oil",
                raw_payload_json={"test": True},
                provider_kind="live",
            ),
            CollectorStatus(
                provider="polymarket",
                data_type="prediction_market",
                status="ok",
                provider_kind="live",
                message="test status",
                created_at=now,
            ),
        ]
    )
    event = AnomalyEvent(
        topic="iran_oil",
        market_id=market.id,
        signal_score=0.91,
        odds_jump=0.25,
        volume_z_score=3.0,
        asset_confirmation_score=0.9,
        headline_gap_minutes=30.0,
        started_at=now - timedelta(minutes=30),
        ended_at=now,
        created_at=now,
        explanation_json={
            "market_title": market.title,
            "scores": {
                "odds_jump": 1.0,
                "volume_spike": 1.0,
                "asset_confirmation": 0.9,
                "headline_gap": 1.0,
                "topic_importance": 1.0,
            },
            "had_headline_before": False,
            "provenance": {
                "alert_label": "LIVE",
                "components": {
                    "odds_jump": {
                        "provider_kinds": ["live"],
                        "sources": ["polymarket"],
                    }
                },
                "stale_reasons": [],
            },
        },
    )
    db_session.add(event)
    db_session.commit()
    return event


def _session_factory(db_session):
    return sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


def test_analyst_context_assembly(db_session) -> None:
    event = _create_event_fixture(db_session)

    context = assemble_analyst_context(db_session, event)

    assert context.schema_version == "analyst_context.v1"
    assert context.event.id == event.id
    assert context.market is not None
    assert context.market.source == "polymarket"
    assert context.provenance.alert_label == "LIVE"
    assert len(context.prediction_snapshots) == 2
    assert len(context.asset_snapshots) == 2
    assert len(context.headline_timeline) == 1
    assert len(context.provider_statuses) == 1


def test_deterministic_analyst_backend_uses_safe_wording(db_session) -> None:
    event = _create_event_fixture(db_session)
    context = assemble_analyst_context(db_session, event)

    result = DeterministicAnalystBackend().analyze(context)

    assert result.backend == "deterministic"
    assert result.status == "success"
    assert "manual review" in result.summary_text
    assert "not an accusation" in result.summary_text
    forbidden_phrases = ["insider trading happened", "someone knew", "tradable"]
    assert all(phrase not in result.summary_text.lower() for phrase in forbidden_phrases)


def test_hermes_backend_disabled_does_not_call_network(db_session) -> None:
    event = _create_event_fixture(db_session)
    context = assemble_analyst_context(db_session, event)

    result = HermesAnalystBackend(enabled=False, endpoint=None).analyze(context)

    assert result.backend == "hermes"
    assert result.status == "disabled"
    assert result.report_json["payload_validated"] is True
    assert "no Hermes network call was made" in result.summary_text


def test_analyst_report_persistence(db_session) -> None:
    event = _create_event_fixture(db_session)
    context = assemble_analyst_context(db_session, event)
    result = DeterministicAnalystBackend().analyze(context)
    db_session.add(
        AnalystReport(
            anomaly_event_id=event.id,
            backend=result.backend,
            status=result.status,
            report_json=result.report_json,
            summary_text=result.summary_text,
        )
    )
    db_session.commit()

    report = db_session.scalar(
        select(AnalystReport).where(AnalystReport.backend == "deterministic")
    )
    assert report is not None
    assert report.status == "success"
    assert report.anomaly_event_id == event.id


def test_analyst_runner_handles_no_events(db_session) -> None:
    created = run_analyst_once(
        session_factory=_session_factory(db_session),
        backend=DeterministicAnalystBackend(),
    )

    assert created == 0
    status = db_session.scalars(select(CollectorStatus).order_by(CollectorStatus.id)).all()[-1]
    assert status.provider == "analyst"
    assert status.status == "no_events"


def test_analyst_runner_creates_report_for_event(db_session) -> None:
    event = _create_event_fixture(db_session)

    created = run_analyst_once(
        session_factory=_session_factory(db_session),
        backend=DeterministicAnalystBackend(),
    )
    db_session.expire_all()

    assert created == 1
    report = db_session.scalar(
        select(AnalystReport).where(AnalystReport.anomaly_event_id == event.id)
    )
    assert report is not None
    assert report.backend == "deterministic"
    assert report.status == "success"

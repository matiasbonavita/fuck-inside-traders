from __future__ import annotations

import argparse
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fuck_inside_traders.logging_config import configure_logging
from fuck_inside_traders.reports.analyst import AnalystBackend, get_analyst_backend
from fuck_inside_traders.reports.context import assemble_analyst_context
from fuck_inside_traders.storage.database import SessionLocal, init_db, session_scope
from fuck_inside_traders.storage.models import AnalystReport, AnomalyEvent, CollectorStatus

logger = logging.getLogger(__name__)


def _events_without_report(
    session: Session,
    backend_name: str,
    limit: int,
) -> list[AnomalyEvent]:
    has_report = (
        select(AnalystReport.id)
        .where(
            AnalystReport.anomaly_event_id == AnomalyEvent.id,
            AnalystReport.backend == backend_name,
        )
        .exists()
    )
    return list(
        session.scalars(
            select(AnomalyEvent)
            .where(~has_report)
            .order_by(AnomalyEvent.created_at.asc())
            .limit(limit)
        )
    )


def run_analyst_once(
    *,
    limit: int = 25,
    backend: AnalystBackend | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> int:
    backend = backend or get_analyst_backend()
    reports_created = 0
    with session_scope(session_factory) as session:
        events = _events_without_report(session, backend.name, limit)
        for event in events:
            context = assemble_analyst_context(session, event)
            result = backend.analyze(context)
            session.add(
                AnalystReport(
                    anomaly_event_id=event.id,
                    backend=result.backend,
                    status=result.status,
                    report_json=result.report_json,
                    summary_text=result.summary_text,
                )
            )
            reports_created += 1
            logger.info(
                "Analyst report created event_id=%s backend=%s status=%s",
                event.id,
                result.backend,
                result.status,
            )

        status = "created_reports" if reports_created else "no_events"
        message = (
            f"Analyst run complete backend={backend.name} "
            f"events_found={len(events)} reports_created={reports_created}"
        )
        session.add(
            CollectorStatus(
                provider="analyst",
                data_type="analyst_report",
                status=status,
                provider_kind="system",
                message=message,
            )
        )
    logger.info(message)
    return reports_created


def main() -> None:
    parser = argparse.ArgumentParser(description="Run optional analyst/reporting layer.")
    parser.add_argument("--once", action="store_true", help="Run analyst once and exit.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum events to process.")
    parser.add_argument(
        "--interval-minutes",
        type=float,
        default=5.0,
        help="Minutes between analyst runs when not using --once.",
    )
    args = parser.parse_args()

    configure_logging()
    init_db()

    if args.once:
        run_analyst_once(limit=args.limit)
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_analyst_once,
        "interval",
        kwargs={"limit": args.limit},
        minutes=args.interval_minutes,
        next_run_time=None,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("Analyst scheduler started interval_minutes=%s", args.interval_minutes)
    try:
        run_analyst_once(limit=args.limit)
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Analyst scheduler stopped")
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()

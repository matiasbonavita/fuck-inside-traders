from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from fuck_inside_traders.logging_config import configure_logging
from fuck_inside_traders.scripts.run_collectors import collect_once
from fuck_inside_traders.scripts.run_detector import detect_once
from fuck_inside_traders.storage.database import init_db, session_scope
from fuck_inside_traders.storage.models import CollectorStatus

logger = logging.getLogger(__name__)


def monitor_cycle() -> None:
    started_at = datetime.now(UTC)
    logger.info("Monitor cycle started")
    try:
        collect_once()
        events_created = detect_once()
    except Exception as exc:
        logger.exception("Monitor cycle failed error=%s", exc)
        with session_scope() as session:
            session.add(
                CollectorStatus(
                    provider="monitor",
                    data_type="monitor",
                    status="failed",
                    provider_kind="system",
                    message=f"Monitor cycle failed error={exc}",
                )
            )
        raise

    elapsed_seconds = (datetime.now(UTC) - started_at).total_seconds()
    message = (
        f"Monitor cycle complete events_created={events_created} "
        f"elapsed_seconds={elapsed_seconds:.1f}"
    )
    with session_scope() as session:
        session.add(
            CollectorStatus(
                provider="monitor",
                data_type="monitor",
                status="ok",
                provider_kind="system",
                message=message,
            )
        )
    logger.info(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run local FIT monitoring cycles: collect public data, then detect anomalies."
    )
    parser.add_argument("--once", action="store_true", help="Run one monitor cycle and exit.")
    parser.add_argument(
        "--interval-minutes",
        type=float,
        default=5.0,
        help="Minutes between monitor cycles when not using --once.",
    )
    args = parser.parse_args()

    configure_logging()
    init_db()

    if args.once:
        monitor_cycle()
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        monitor_cycle,
        "interval",
        minutes=args.interval_minutes,
        next_run_time=None,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("Monitor scheduler started interval_minutes=%s", args.interval_minutes)
    try:
        monitor_cycle()
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Monitor scheduler stopped")
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()

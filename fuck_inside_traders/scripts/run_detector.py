from __future__ import annotations

import argparse
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

from fuck_inside_traders.alerts.telegram import TelegramAlerter
from fuck_inside_traders.detectors.engine import AnomalyDetector
from fuck_inside_traders.logging_config import configure_logging
from fuck_inside_traders.storage.database import init_db, session_scope
from fuck_inside_traders.storage.models import CollectorStatus

logger = logging.getLogger(__name__)


def detect_once() -> int:
    with session_scope() as session:
        events = AnomalyDetector().run_once(session)
        alerter = TelegramAlerter()
        for event in events:
            alerter.send(event, event.market)
        session.add(
            CollectorStatus(
                provider="detector",
                data_type="detector",
                status="created_events" if events else "no_alerts",
                provider_kind="system",
                message=f"Detector run complete events_created={len(events)}",
            )
        )
    logger.info("Detector run complete events_created=%s", len(events))
    return len(events)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic anomaly detector.")
    parser.add_argument("--once", action="store_true", help="Run detector once and exit.")
    args = parser.parse_args()

    configure_logging()
    init_db()

    if args.once:
        detect_once()
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(detect_once, "interval", minutes=5, next_run_time=None)
    scheduler.start()
    logger.info("Detector scheduler started interval_minutes=5")
    try:
        detect_once()
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Detector scheduler stopped")
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()

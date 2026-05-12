from __future__ import annotations

import argparse
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

from fuck_inside_traders.collectors.assets import AssetPriceCollector
from fuck_inside_traders.collectors.news import NewsCollector
from fuck_inside_traders.collectors.polymarket import PolymarketCollector
from fuck_inside_traders.config import load_rss_feeds, load_topics
from fuck_inside_traders.logging_config import configure_logging
from fuck_inside_traders.storage.database import init_db, session_scope

logger = logging.getLogger(__name__)


def collect_once() -> None:
    topics = load_topics()
    feeds = load_rss_feeds()
    with session_scope() as session:
        prediction_count = PolymarketCollector().collect(session, topics)
        asset_count = AssetPriceCollector().collect(session, topics)
        news_count = NewsCollector().collect(session, topics, feeds)
    logger.info(
        "Collector run complete prediction_snapshots=%s asset_snapshots=%s news_items=%s",
        prediction_count,
        asset_count,
        news_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run public data collectors.")
    parser.add_argument("--once", action="store_true", help="Run collectors once and exit.")
    args = parser.parse_args()

    configure_logging()
    init_db()

    if args.once:
        collect_once()
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(collect_once, "interval", minutes=5, next_run_time=None)
    scheduler.start()
    logger.info("Collector scheduler started interval_minutes=5")
    try:
        collect_once()
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Collector scheduler stopped")
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()

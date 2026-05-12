from __future__ import annotations

import argparse

from fuck_inside_traders.collectors.polymarket import PolymarketHttpProvider
from fuck_inside_traders.config import load_topics
from fuck_inside_traders.logging_config import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review Polymarket public-search candidates for manual watchlist updates.",
    )
    parser.add_argument("--topic", help="Configured topic name to review, for example iran_oil.")
    args = parser.parse_args()

    configure_logging()
    topics = load_topics()
    if args.topic:
        topics = [topic for topic in topics if topic.name == args.topic]
    if not topics:
        raise SystemExit(f"No configured topics matched: {args.topic}")

    provider = PolymarketHttpProvider(watchlist={})
    provider.fetch_markets(topics)

    print(
        "\t".join(
            [
                "topic",
                "query",
                "accepted",
                "active",
                "closed",
                "score",
                "reason",
                "external_id",
                "slug",
                "title",
            ]
        )
    )
    for candidate in provider.discovery_candidates:
        print(
            "\t".join(
                [
                    candidate.topic,
                    candidate.query or "",
                    str(candidate.accepted),
                    str(candidate.active),
                    str(candidate.closed),
                    f"{candidate.relevance_score:.2f}",
                    candidate.rejection_reason or "accepted",
                    candidate.external_id or "",
                    candidate.slug or "",
                    candidate.title.replace("\t", " "),
                ]
            )
        )


if __name__ == "__main__":
    main()

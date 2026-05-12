from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path("config")


@dataclass(frozen=True)
class Topic:
    name: str
    keywords: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    topic_importance: float = 1.0
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    polymarket_queries: list[str] = field(default_factory=list)
    polymarket_allowlist: list[str] = field(default_factory=list)
    polymarket_blocklist: list[str] = field(default_factory=list)
    min_market_relevance_score: float = 0.55


@dataclass(frozen=True)
class Thresholds:
    alert_threshold: float
    watch_threshold: float
    windows_minutes: list[int]
    polymarket_relevance_threshold: float = 0.55
    polymarket_negative_keywords: list[str] = field(default_factory=list)
    polymarket_relevant_categories: list[str] = field(default_factory=list)
    prediction_market_fresh_minutes: int = 30
    asset_price_fresh_minutes: int = 30
    headline_fresh_minutes: int = 360
    detector_fresh_minutes: int = 30


@dataclass(frozen=True)
class RssFeed:
    name: str
    url: str


@dataclass(frozen=True)
class PolymarketWatchlistEntry:
    topic: str
    slug: str | None = None
    external_id: str | None = None
    url: str | None = None
    description: str | None = None
    active: bool = True


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        msg = f"Expected mapping in {path}"
        raise ValueError(msg)
    return data


def load_topics(path: Path = CONFIG_DIR / "topics.yaml") -> list[Topic]:
    data = _load_yaml(path)
    topics = []
    for name, raw_topic in (data.get("topics") or {}).items():
        topics.append(
            Topic(
                name=name,
                keywords=list(raw_topic.get("keywords") or []),
                include_keywords=list(raw_topic.get("include_keywords") or []),
                exclude_keywords=list(raw_topic.get("exclude_keywords") or []),
                polymarket_queries=list(raw_topic.get("polymarket_queries") or []),
                polymarket_allowlist=list(raw_topic.get("polymarket_allowlist") or []),
                polymarket_blocklist=list(raw_topic.get("polymarket_blocklist") or []),
                assets=list(raw_topic.get("assets") or []),
                topic_importance=float(raw_topic.get("topic_importance", 1.0)),
                min_market_relevance_score=float(
                    raw_topic.get("min_market_relevance_score", 0.55)
                ),
            )
        )
    return topics


def load_thresholds(path: Path = CONFIG_DIR / "thresholds.yaml") -> Thresholds:
    data = _load_yaml(path)
    return Thresholds(
        alert_threshold=float(data.get("alert_threshold", 0.80)),
        watch_threshold=float(data.get("watch_threshold", 0.65)),
        windows_minutes=[int(window) for window in data.get("windows_minutes", [5, 15, 30])],
        polymarket_relevance_threshold=float(data.get("polymarket_relevance_threshold", 0.55)),
        polymarket_negative_keywords=[
            str(keyword) for keyword in data.get("polymarket_negative_keywords", [])
        ],
        polymarket_relevant_categories=[
            str(category) for category in data.get("polymarket_relevant_categories", [])
        ],
        prediction_market_fresh_minutes=int(data.get("prediction_market_fresh_minutes", 30)),
        asset_price_fresh_minutes=int(data.get("asset_price_fresh_minutes", 30)),
        headline_fresh_minutes=int(data.get("headline_fresh_minutes", 360)),
        detector_fresh_minutes=int(data.get("detector_fresh_minutes", 30)),
    )


def load_rss_feeds(path: Path = CONFIG_DIR / "rss_feeds.yaml") -> list[RssFeed]:
    data = _load_yaml(path)
    return [
        RssFeed(name=str(feed["name"]), url=str(feed["url"]))
        for feed in data.get("feeds", [])
        if "name" in feed and "url" in feed
    ]


def load_polymarket_watchlist(
    path: Path = CONFIG_DIR / "polymarket_watchlist.yaml",
) -> dict[str, list[PolymarketWatchlistEntry]]:
    if not path.exists():
        return {}

    data = _load_yaml(path)
    watchlist: dict[str, list[PolymarketWatchlistEntry]] = {}
    for topic_name, raw_topic in (data.get("topics") or {}).items():
        entries = []
        for raw_entry in raw_topic.get("markets") or []:
            if not isinstance(raw_entry, dict):
                continue
            entries.append(
                PolymarketWatchlistEntry(
                    topic=str(topic_name),
                    slug=str(raw_entry["slug"]).strip() if raw_entry.get("slug") else None,
                    external_id=str(raw_entry["external_id"]).strip()
                    if raw_entry.get("external_id")
                    else None,
                    url=str(raw_entry["url"]).strip() if raw_entry.get("url") else None,
                    description=str(raw_entry["description"]).strip()
                    if raw_entry.get("description")
                    else None,
                    active=bool(raw_entry.get("active", True)),
                )
            )
        watchlist[str(topic_name)] = entries
    return watchlist

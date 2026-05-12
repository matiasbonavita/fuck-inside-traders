from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from fuck_inside_traders.collectors.assets import (
    AssetPriceCollector,
    AssetPriceRecord,
    FallbackAssetPriceProvider,
)
from fuck_inside_traders.collectors.news import (
    NewsCollector,
    NewsRecord,
    get_headline_timeline,
    had_matching_headline_before,
)
from fuck_inside_traders.collectors.polymarket import (
    PolymarketCollector,
    PolymarketDiscoveryRecord,
    PolymarketHttpProvider,
    PredictionMarketRecord,
    market_relevance_score,
    score_polymarket_relevance,
)
from fuck_inside_traders.config import (
    PolymarketWatchlistEntry,
    RssFeed,
    Topic,
    load_polymarket_watchlist,
)
from fuck_inside_traders.provenance import LIVE, MOCK, UNKNOWN
from fuck_inside_traders.storage.models import (
    AssetPriceSnapshot,
    CollectorStatus,
    Market,
    NewsItem,
    PolymarketDiscoveryCandidate,
    PredictionMarketSnapshot,
)


def topic() -> Topic:
    return Topic(
        name="iran_oil",
        keywords=["iran", "oil"],
        include_keywords=["iran", "oil", "hormuz"],
        exclude_keywords=["fifa", "world cup", "soccer"],
        polymarket_queries=["iran oil", "hormuz oil"],
        assets=["USO"],
        topic_importance=1.0,
    )


def test_polymarket_collector_with_mocked_http_response(db_session) -> None:
    seen_queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public-search"
        seen_queries.append(request.url.params.get("q"))
        return httpx.Response(
            200,
            json={"events": [
                {
                    "id": "pm-1",
                    "title": "Will iran oil exports be disrupted this week?",
                    "slug": "iran-oil",
                    "active": True,
                    "closed": False,
                    "markets": [
                        {
                            "id": "pm-1",
                            "question": "Will iran oil exports be disrupted this week?",
                            "slug": "iran-oil",
                            "active": True,
                            "closed": False,
                            "outcomePrices": "[\"0.62\", \"0.38\"]",
                            "volume": "40000",
                            "liquidity": "10000",
                            "bestBid": "0.61",
                            "bestAsk": "0.63",
                        }
                    ],
                }
            ]},
        )

    provider = PolymarketHttpProvider(
        base_url="https://mock.polymarket.local",
        transport=httpx.MockTransport(handler),
        watchlist={},
    )
    count = PolymarketCollector(provider=provider).collect(db_session, [topic()])
    db_session.commit()

    assert count == 1
    assert seen_queries == ["iran oil", "hormuz oil"]
    assert db_session.scalar(select(func.count()).select_from(Market)) == 1
    assert db_session.scalar(select(func.count()).select_from(PredictionMarketSnapshot)) == 2
    assert db_session.scalar(select(func.count()).select_from(PolymarketDiscoveryCandidate)) == 2


def test_polymarket_watchlist_config_parsing(tmp_path) -> None:
    path = tmp_path / "polymarket_watchlist.yaml"
    path.write_text(
        """
topics:
  iran_oil:
    markets:
      - slug: strait-of-hormuz-traffic-returns-to-normal-by-may-15
        external_id: "2054133"
        url: https://polymarket.com/event/strait-of-hormuz-traffic-returns-to-normal-by-may-15
        description: Curated Hormuz market.
        active: true
""",
        encoding="utf-8",
    )

    watchlist = load_polymarket_watchlist(path)

    assert watchlist["iran_oil"][0].slug == "strait-of-hormuz-traffic-returns-to-normal-by-may-15"
    assert watchlist["iran_oil"][0].external_id == "2054133"
    assert watchlist["iran_oil"][0].active is True


def test_polymarket_fetches_watchlisted_market(db_session) -> None:
    watchlist = {
        "iran_oil": [
            PolymarketWatchlistEntry(
                topic="iran_oil",
                slug="strait-of-hormuz-traffic-returns-to-normal-by-may-15",
                active=True,
            )
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            "/events/slug/strait-of-hormuz-traffic-returns-to-normal-by-may-15"
        )
        return httpx.Response(
            200,
            json={
                "id": "event-1",
                "title": "Strait of Hormuz traffic returns to normal by May 15?",
                "slug": "strait-of-hormuz-traffic-returns-to-normal-by-may-15",
                "active": True,
                "closed": False,
                "markets": [
                    {
                        "id": "2054133",
                        "question": "Strait of Hormuz traffic returns to normal by May 15?",
                        "slug": "strait-of-hormuz-traffic-returns-to-normal-by-may-15",
                        "active": True,
                        "closed": False,
                        "outcomePrices": "[\"0.25\", \"0.75\"]",
                        "volume": "1000",
                    }
                ],
            },
        )

    provider = PolymarketHttpProvider(
        base_url="https://mock.polymarket.local",
        transport=httpx.MockTransport(handler),
        watchlist=watchlist,
    )

    count = PolymarketCollector(provider=provider).collect(db_session, [topic()])
    candidate = db_session.scalar(select(PolymarketDiscoveryCandidate))

    assert count == 1
    assert candidate is not None
    assert candidate.accepted is True
    assert candidate.query == "watchlist"


def test_polymarket_watchlist_prefers_external_id_market_fetch(db_session) -> None:
    watchlist = {
        "iran_oil": [
            PolymarketWatchlistEntry(
                topic="iran_oil",
                slug="strait-of-hormuz-traffic-returns-to-normal-by-may-15",
                external_id="2054133",
                active=True,
            )
        ]
    }
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        assert request.url.path == "/markets/2054133"
        return httpx.Response(
            200,
            json={
                "id": "2054133",
                "question": "Strait of Hormuz traffic returns to normal by May 15?",
                "slug": "strait-of-hormuz-traffic-returns-to-normal-by-may-15",
                "active": True,
                "closed": False,
                "outcomePrices": "[\"0.25\", \"0.75\"]",
                "volume": "1000",
            },
        )

    provider = PolymarketHttpProvider(
        base_url="https://mock.polymarket.local",
        transport=httpx.MockTransport(handler),
        watchlist=watchlist,
    )

    count = PolymarketCollector(provider=provider).collect(db_session, [topic()])

    assert count == 1
    assert seen_paths == ["/markets/2054133"]


def test_polymarket_collector_deactivates_absent_live_markets(db_session) -> None:
    db_session.add(
        Market(
            source="polymarket",
            external_id="old-market",
            title="Old watchlist market",
            topic="iran_oil",
            active=True,
        )
    )
    db_session.flush()

    class OneLiveMarketProvider:
        def fetch_markets(self, topics: list[Topic]) -> list[PredictionMarketRecord]:
            return [
                PredictionMarketRecord(
                    source="polymarket",
                    provider_kind=LIVE,
                    external_id="current-market",
                    title="Will Iran oil exports be disrupted?",
                    topic=topics[0].name,
                    url=None,
                    active=True,
                    probability=0.55,
                    volume=1000.0,
                    liquidity=None,
                    bid=None,
                    ask=None,
                    timestamp=datetime.now(UTC),
                )
            ]

    count = PolymarketCollector(provider=OneLiveMarketProvider()).collect(db_session, [topic()])
    db_session.flush()

    old_market = db_session.scalar(select(Market).where(Market.external_id == "old-market"))
    current_market = db_session.scalar(
        select(Market).where(Market.external_id == "current-market")
    )

    assert count == 1
    assert old_market is not None
    assert old_market.active is False
    assert current_market is not None
    assert current_market.active is True


def test_polymarket_watchlist_closed_market_is_rejected(db_session) -> None:
    watchlist = {
        "iran_oil": [
            PolymarketWatchlistEntry(
                topic="iran_oil",
                slug="closed-hormuz-market",
                active=True,
            )
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events/slug/closed-hormuz-market"
        return httpx.Response(
            200,
            json={
                "id": "event-closed",
                "title": "Strait of Hormuz traffic returns to normal?",
                "slug": "closed-hormuz-market",
                "active": True,
                "closed": True,
                "markets": [
                    {
                        "id": "closed-1",
                        "question": "Strait of Hormuz traffic returns to normal?",
                        "slug": "closed-hormuz-market",
                        "active": True,
                        "closed": True,
                        "outcomePrices": "[\"0\", \"1\"]",
                    }
                ],
            },
        )

    provider = PolymarketHttpProvider(
        base_url="https://mock.polymarket.local",
        transport=httpx.MockTransport(handler),
        watchlist=watchlist,
    )

    PolymarketCollector(provider=provider).collect(db_session, [topic()])
    db_session.flush()
    candidate = db_session.scalar(
        select(PolymarketDiscoveryCandidate).where(
            PolymarketDiscoveryCandidate.external_id == "closed-1"
        )
    )

    assert candidate is not None
    assert candidate.accepted is False
    assert candidate.closed is True
    assert candidate.rejection_reason == "closed"
    assert db_session.scalar(select(Market).where(Market.external_id == "closed-1")) is None


def test_polymarket_watchlist_inactive_entry_is_rejected(db_session) -> None:
    watchlist = {
        "iran_oil": [
            PolymarketWatchlistEntry(
                topic="iran_oil",
                slug="inactive-watchlist-entry",
                active=False,
            )
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events/slug/inactive-watchlist-entry"
        return httpx.Response(
            200,
            json={
                "id": "event-inactive",
                "title": "Will Iran oil exports be disrupted?",
                "slug": "inactive-watchlist-entry",
                "active": True,
                "closed": False,
                "markets": [
                    {
                        "id": "inactive-1",
                        "question": "Will Iran oil exports be disrupted?",
                        "slug": "inactive-watchlist-entry",
                        "active": True,
                        "closed": False,
                        "outcomePrices": "[\"0.4\", \"0.6\"]",
                    }
                ],
            },
        )

    provider = PolymarketHttpProvider(
        base_url="https://mock.polymarket.local",
        transport=httpx.MockTransport(handler),
        watchlist=watchlist,
    )

    PolymarketCollector(provider=provider).collect(db_session, [topic()])
    db_session.flush()
    candidate = db_session.scalar(
        select(PolymarketDiscoveryCandidate).where(
            PolymarketDiscoveryCandidate.external_id == "inactive-1"
        )
    )

    assert candidate is not None
    assert candidate.accepted is False
    assert candidate.rejection_reason == "watchlist_entry_inactive"
    assert db_session.scalar(select(Market).where(Market.external_id == "inactive-1")) is None


def test_polymarket_allowlist_accepts_low_relevance_market(db_session) -> None:
    tracked_topic = Topic(
        name="iran_oil",
        keywords=["iran", "oil"],
        polymarket_queries=["custom"],
        polymarket_allowlist=["allowed-market"],
        polymarket_blocklist=[],
        min_market_relevance_score=0.99,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public-search"
        return httpx.Response(
            200,
            json={"events": [
                {
                    "id": "allowed-market",
                    "question": "Opaque custom market title",
                    "active": True,
                    "closed": False,
                    "outcomePrices": "[\"0.55\", \"0.45\"]",
                }
            ]},
        )

    provider = PolymarketHttpProvider(
        base_url="https://mock.polymarket.local",
        transport=httpx.MockTransport(handler),
        watchlist={},
    )
    count = PolymarketCollector(provider=provider).collect(db_session, [tracked_topic])

    assert count == 1


def test_polymarket_blocklist_rejects_otherwise_relevant_market(db_session) -> None:
    tracked_topic = Topic(
        name="iran_oil",
        keywords=["iran", "oil"],
        include_keywords=["iran", "oil"],
        polymarket_queries=["iran oil"],
        polymarket_blocklist=["blocked-market"],
        min_market_relevance_score=0.1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public-search"
        return httpx.Response(
            200,
            json={"events": [
                {
                    "id": "blocked-market",
                    "question": "Will Iran oil exports be disrupted?",
                    "active": True,
                    "closed": False,
                    "outcomePrices": "[\"0.55\", \"0.45\"]",
                }
            ]},
        )

    provider = PolymarketHttpProvider(
        base_url="https://mock.polymarket.local",
        transport=httpx.MockTransport(handler),
        watchlist={},
    )
    count = PolymarketCollector(provider=provider).collect(db_session, [tracked_topic])

    assert count == 1
    db_session.flush()
    market = db_session.scalar(select(Market).where(Market.external_id == "blocked-market"))
    assert market is None
    status = db_session.scalars(select(CollectorStatus)).all()[-1]
    assert status.status == "mock_backed"
    assert status.provider_kind == MOCK
    candidate = db_session.scalar(
        select(PolymarketDiscoveryCandidate).where(
            PolymarketDiscoveryCandidate.external_id == "blocked-market"
        )
    )
    assert candidate is not None
    assert candidate.accepted is False
    assert candidate.rejection_reason == "blocklisted"


class FakeAssetProvider:
    def fetch_latest(self, topics: list[Topic]) -> list[AssetPriceRecord]:
        now = datetime.now(UTC)
        return [
            AssetPriceRecord(
                symbol="USO",
                source="fake_asset",
                provider_kind=LIVE,
                topic=topics[0].name,
                price=82.0,
                volume=1_000_000.0,
                timestamp=now,
            )
        ]


def test_asset_collector_with_mocked_provider_output(db_session) -> None:
    count = AssetPriceCollector(
        provider=FakeAssetProvider(),
        fallback_provider=FallbackAssetPriceProvider(),
    ).collect(db_session, [topic()])
    db_session.commit()

    assert count == 1
    assert db_session.scalar(select(func.count()).select_from(AssetPriceSnapshot)) == 2


class FakeNewsProvider:
    def fetch_news(self, topics: list[Topic], feeds: list[RssFeed]) -> list[NewsRecord]:
        return [
            NewsRecord(
                source="fake",
                provider_kind=LIVE,
                title="Iran oil headline",
                url="https://example.local/iran-oil",
                published_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                topic=topics[0].name,
                raw_payload_json={"fake": True},
            )
        ]


def test_news_collector_deduplicates_and_supports_timeline_helpers(db_session) -> None:
    collector = NewsCollector(provider=FakeNewsProvider())
    feeds = [RssFeed(name="fake", url="https://example.local/rss")]

    assert collector.collect(db_session, [topic()], feeds) == 1
    assert collector.collect(db_session, [topic()], feeds) == 0
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(NewsItem)) == 1
    assert had_matching_headline_before(
        "iran_oil",
        datetime(2026, 1, 1, 12, 5, tzinfo=UTC),
        10,
        session=db_session,
    )
    timeline = get_headline_timeline(
        "iran_oil",
        datetime(2026, 1, 1, 11, 55, tzinfo=UTC),
        datetime(2026, 1, 1, 12, 5, tzinfo=UTC),
        session=db_session,
    )
    assert len(timeline) == 1


def test_polymarket_relevance_rejects_sports_markets() -> None:
    tracked_topic = topic()

    assert market_relevance_score("Will Iran oil exports be disrupted?", tracked_topic) >= 0.55
    assert market_relevance_score("Will Iran win the 2026 FIFA World Cup?", tracked_topic) == 0.0


def test_polymarket_relevance_scores_positive_market_fields() -> None:
    tracked_topic = topic()
    raw_market = {
        "question": "Will OPEC sanctions disrupt oil flows near Hormuz?",
        "description": "Iran and Israel tensions may affect energy shipping.",
        "slug": "opec-sanctions-oil-hormuz",
        "category": "Energy",
        "outcomes": "[\"Yes\", \"No\"]",
    }

    result = score_polymarket_relevance(raw_market, tracked_topic)

    assert result.score >= 0.55
    assert "oil" in result.matched_keywords
    assert result.reason.startswith("accepted")


def test_polymarket_relevance_scores_negative_market_fields() -> None:
    tracked_topic = topic()
    negative_titles = [
        "New Rihanna album before GTA VI?",
        "Will Iran win the 2026 FIFA World Cup?",
        "NBA champion market",
        "Taylor Swift movie announcement?",
    ]

    for title in negative_titles:
        result = score_polymarket_relevance(
            {"question": title, "category": "Sports"},
            tracked_topic,
        )
        assert result.score == 0.0
        assert result.reason.startswith("negative_keyword")


class EmptyPredictionMarketProvider:
    discovery_candidates = [
        PolymarketDiscoveryRecord(
            topic="iran_oil",
            query="fixture",
            source="polymarket",
            external_id="fixture-rejected",
            slug="fixture-rejected",
            title="Rejected fixture",
            url=None,
            active=True,
            closed=False,
            accepted=False,
            relevance_score=0.0,
            rejection_reason="score_below_threshold:0.00<0.55",
            provider_kind=LIVE,
            raw_payload_json={},
        )
    ]

    def fetch_markets(self, topics: list[Topic]):
        return []


def test_polymarket_no_live_and_no_mock_records_unknown_status(db_session) -> None:
    count = PolymarketCollector(
        provider=EmptyPredictionMarketProvider(),
        fallback_provider=EmptyPredictionMarketProvider(),
    ).collect(db_session, [topic()])
    db_session.flush()

    status = db_session.scalars(select(CollectorStatus)).all()[-1]

    assert count == 0
    assert status.status == "no_live_candidates"
    assert status.provider_kind == UNKNOWN
    assert "fallback_attempted=True" in (status.message or "")
    assert "mock_data_used=False" in (status.message or "")


def test_public_news_provider_handles_gdelt_429_without_crashing() -> None:
    from fuck_inside_traders.collectors.news import PublicNewsProvider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    provider = PublicNewsProvider(
        gdelt_base_url="https://mock.gdelt.local",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
        gdelt_enabled=True,
    )

    records = provider.fetch_news([topic()], [])

    assert records == []
    assert provider.last_statuses[0]["status"] == "rate_limited"


def test_public_news_provider_records_gdelt_non_json_preview() -> None:
    from fuck_inside_traders.collectors.news import PublicNewsProvider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html>not json</html>",
            headers={"content-type": "text/html"},
        )

    provider = PublicNewsProvider(
        gdelt_base_url="https://mock.gdelt.local",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
        gdelt_enabled=True,
    )

    records = provider.fetch_news([topic()], [])

    assert records == []
    assert provider.last_statuses[0]["status"] == "invalid_response"
    assert "content_type=text/html" in provider.last_statuses[0]["message"]
    assert "not json" in provider.last_statuses[0]["message"]


def test_public_news_provider_can_disable_gdelt(monkeypatch) -> None:
    from fuck_inside_traders.collectors.news import PublicNewsProvider

    monkeypatch.setenv("GDELT_ENABLED", "false")
    provider = PublicNewsProvider(gdelt_base_url="https://mock.gdelt.local")

    records = provider.fetch_news([topic()], [])

    assert records == []
    assert provider.last_statuses[0]["status"] == "disabled"


def test_collectors_mark_fallback_provider_kind(db_session) -> None:
    count = AssetPriceCollector(
        provider=FallbackAssetPriceProvider(),
        fallback_provider=FallbackAssetPriceProvider(),
    ).collect(db_session, [topic()])

    assert count == 1
    snapshot = db_session.scalars(select(AssetPriceSnapshot)).all()[-1]
    assert snapshot.provider_kind == MOCK

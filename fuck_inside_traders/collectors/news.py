from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import feedparser
import httpx
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from fuck_inside_traders.config import RssFeed, Topic, load_rss_feeds, load_topics
from fuck_inside_traders.provenance import LIVE, MOCK
from fuck_inside_traders.settings import get_settings
from fuck_inside_traders.storage.database import session_scope
from fuck_inside_traders.storage.models import CollectorStatus, NewsItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsRecord:
    source: str
    provider_kind: str
    title: str
    url: str | None
    published_at: datetime
    topic: str
    raw_payload_json: dict[str, Any] | None


class NewsProvider(Protocol):
    def fetch_news(self, topics: list[Topic], feeds: list[RssFeed]) -> list[NewsRecord]:
        pass


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_gdelt_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M%S%z"):
        try:
            parsed = datetime.strptime(value, fmt)
            return _ensure_aware(parsed)
        except ValueError:
            continue
    try:
        return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return datetime.now(UTC)


def _entry_published_at(entry: Any) -> datetime:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        return datetime(*parsed[:6], tzinfo=UTC)
    return datetime.now(UTC)


def _matches_topic(text: str, topic: Topic) -> bool:
    lower_text = text.lower()
    return any(keyword.lower() in lower_text for keyword in topic.keywords)


class PublicNewsProvider:
    def __init__(
        self,
        gdelt_base_url: str | None = None,
        timeout: float = 12.0,
        transport: httpx.BaseTransport | None = None,
        backoff_seconds: float = 1.0,
    ) -> None:
        self.gdelt_base_url = gdelt_base_url or get_settings().gdelt_api_base_url
        self.timeout = timeout
        self.transport = transport
        self.backoff_seconds = backoff_seconds
        self.last_statuses: list[dict[str, str]] = []
        self._gdelt_cache: dict[str, tuple[datetime, list[NewsRecord]]] = {}

    def fetch_news(self, topics: list[Topic], feeds: list[RssFeed]) -> list[NewsRecord]:
        records = self._fetch_gdelt(topics)
        records.extend(self._fetch_rss(topics, feeds))
        return records

    def _fetch_gdelt(self, topics: list[Topic]) -> list[NewsRecord]:
        records: list[NewsRecord] = []
        if not get_settings().gdelt_enabled:
            for topic in topics:
                self._status("gdelt", "headline", "disabled", LIVE, topic.name)
            return records

        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            for topic in topics:
                query_terms = topic.include_keywords or topic.keywords
                query = " OR ".join(query_terms[:5])
                cache_key = f"{topic.name}:{query}"
                cached = self._gdelt_cache.get(cache_key)
                if cached and datetime.now(UTC) - cached[0] < timedelta(minutes=5):
                    records.extend(cached[1])
                    self._status("gdelt", "headline", "cached", LIVE, topic.name)
                    continue
                try:
                    response = self._gdelt_request(client, query)
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        content_type = response.headers.get("content-type", "unknown")
                        preview = response.text[:240].replace("\n", " ").strip()
                        logger.warning(
                            "News fetch returned non-JSON provider=gdelt "
                            "endpoint=%s topic=%s content_type=%s preview=%r error=%s",
                            self.gdelt_base_url,
                            topic.name,
                            content_type,
                            preview,
                            exc,
                        )
                        self._status(
                            "gdelt",
                            "headline",
                            "invalid_response",
                            LIVE,
                            f"{topic.name} content_type={content_type} preview={preview[:120]!r}",
                        )
                        continue
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code == 429:
                        logger.warning(
                            "News fetch rate limited provider=gdelt endpoint=%s topic=%s",
                            self.gdelt_base_url,
                            topic.name,
                        )
                        self._status("gdelt", "headline", "rate_limited", LIVE, topic.name)
                    else:
                        logger.exception(
                            "News fetch failed provider=gdelt endpoint=%s topic=%s error=%s",
                            self.gdelt_base_url,
                            topic.name,
                            exc,
                        )
                        self._status("gdelt", "headline", "network_error", LIVE, topic.name)
                    continue
                except httpx.RequestError as exc:
                    logger.warning(
                        "News fetch network failure provider=gdelt endpoint=%s topic=%s error=%s",
                        self.gdelt_base_url,
                        topic.name,
                        exc,
                    )
                    self._status("gdelt", "headline", "network_error", LIVE, topic.name)
                    continue
                except Exception as exc:
                    logger.exception(
                        "News fetch failed provider=gdelt endpoint=%s topic=%s error=%s",
                        self.gdelt_base_url,
                        topic.name,
                        exc,
                    )
                    self._status("gdelt", "headline", "network_error", LIVE, topic.name)
                    continue

                topic_records = []
                for article in payload.get("articles", []):
                    title = str(article.get("title") or "").strip()
                    if not title or not _matches_topic(title, topic):
                        continue
                    topic_records.append(
                        NewsRecord(
                            source="gdelt",
                            provider_kind=LIVE,
                            title=title,
                            url=article.get("url"),
                            published_at=_parse_gdelt_datetime(
                                article.get("seendate") or article.get("socialimage")
                            ),
                            topic=topic.name,
                            raw_payload_json=article,
                        )
                    )
                records.extend(topic_records)
                self._gdelt_cache[cache_key] = (datetime.now(UTC), topic_records)
                self._status(
                    "gdelt",
                    "headline",
                    "ok",
                    LIVE,
                    f"{topic.name} records={len(topic_records)}",
                )
        return records

    def _gdelt_request(self, client: httpx.Client, query: str) -> httpx.Response:
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": 25,
            "sort": "HybridRel",
            "timespan": "6h",
        }
        response = client.get(self.gdelt_base_url, params=params)
        if response.status_code == 429 and self.backoff_seconds > 0:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else self.backoff_seconds
            except ValueError:
                delay = self.backoff_seconds
            time.sleep(min(delay, 5.0))
            response = client.get(self.gdelt_base_url, params=params)
        response.raise_for_status()
        return response

    def _fetch_rss(self, topics: list[Topic], feeds: list[RssFeed]) -> list[NewsRecord]:
        records: list[NewsRecord] = []
        for feed in feeds:
            try:
                parsed = feedparser.parse(feed.url)
            except Exception as exc:
                logger.exception(
                    "News fetch failed provider=rss feed=%s url=%s error=%s",
                    feed.name,
                    feed.url,
                    exc,
                )
                continue

            if getattr(parsed, "bozo", False):
                log_method = logger.warning if not parsed.entries else logger.info
                log_method(
                    "RSS feed parse issue feed=%s url=%s entries=%s error=%s",
                    feed.name,
                    feed.url,
                    len(parsed.entries),
                    getattr(parsed, "bozo_exception", None),
                )
                if not parsed.entries:
                    self._status("rss", "headline", "parse_error", LIVE, feed.name)
                    continue

            for entry in parsed.entries:
                title = str(getattr(entry, "title", "")).strip()
                link = getattr(entry, "link", None)
                summary = getattr(entry, "summary", "")
                searchable = f"{title} {summary}"
                for topic in topics:
                    if _matches_topic(searchable, topic):
                        records.append(
                            NewsRecord(
                                source=f"rss:{feed.name}",
                                provider_kind=LIVE,
                                title=title,
                                url=str(link) if link else None,
                                published_at=_entry_published_at(entry),
                                topic=topic.name,
                                raw_payload_json={
                                    "title": title,
                                    "link": link,
                                    "summary": summary,
                                },
                            )
                        )
            self._status("rss", "headline", "ok", LIVE, feed.name)
        return records

    def _status(
        self,
        provider: str,
        data_type: str,
        status: str,
        provider_kind: str,
        message: str,
    ) -> None:
        self.last_statuses.append(
            {
                "provider": provider,
                "data_type": data_type,
                "status": status,
                "provider_kind": provider_kind,
                "message": message,
            }
        )


class FallbackNewsProvider:
    def fetch_news(self, topics: list[Topic], feeds: list[RssFeed]) -> list[NewsRecord]:
        now = datetime.now(UTC).replace(microsecond=0)
        records: list[NewsRecord] = []
        for topic in topics:
            records.append(
                NewsRecord(
                    source="mock_news",
                    provider_kind=MOCK,
                    title=f"Energy markets watch {topic.name.replace('_', ' ')} developments",
                    url=f"https://example.local/news/{topic.name}",
                    published_at=now + timedelta(minutes=5),
                    topic=topic.name,
                    raw_payload_json={"fallback": True},
                )
            )
        return records


class NewsCollector:
    def __init__(
        self,
        provider: NewsProvider | None = None,
        fallback_provider: NewsProvider | None = None,
    ) -> None:
        self.provider = provider or PublicNewsProvider()
        self.fallback_provider = fallback_provider or FallbackNewsProvider()

    def collect(
        self,
        session: Session,
        topics: list[Topic] | None = None,
        feeds: list[RssFeed] | None = None,
    ) -> int:
        topics = topics or load_topics()
        feeds = feeds or load_rss_feeds()
        records = self.provider.fetch_news(topics, feeds)
        used_fallback = False
        if not records:
            logger.warning("No live news records found; using fallback provider")
            records = self.fallback_provider.fetch_news(topics, feeds)
            used_fallback = True

        for status in getattr(self.provider, "last_statuses", []):
            session.add(CollectorStatus(**status))

        inserted = 0
        for record in records:
            if self._exists(session, record):
                continue
            session.add(
                NewsItem(
                    source=record.source,
                    title=record.title,
                    url=record.url,
                    published_at=record.published_at,
                    topic=record.topic,
                    raw_payload_json=record.raw_payload_json,
                    provider_kind=record.provider_kind,
                )
            )
            session.flush()
            inserted += 1
        session.add(
            CollectorStatus(
                provider="news_fallback" if used_fallback else "news",
                data_type="headline",
                status="fallback" if used_fallback else "ok",
                provider_kind=MOCK if used_fallback else LIVE,
                message=f"Collected news items inserted={inserted} fetched={len(records)}",
            )
        )
        logger.info("Collected news items inserted=%s fetched=%s", inserted, len(records))
        return inserted

    def _exists(self, session: Session, record: NewsRecord) -> bool:
        conditions = [NewsItem.topic == record.topic]
        if record.url:
            conditions.append(NewsItem.url == record.url)
        else:
            conditions.append(
                and_(NewsItem.title == record.title, NewsItem.published_at == record.published_at)
            )
        return session.scalar(select(NewsItem.id).where(and_(*conditions)).limit(1)) is not None


def had_matching_headline_before(
    topic: str,
    before_timestamp: datetime,
    lookback_minutes: int,
    session: Session | None = None,
) -> bool:
    before_timestamp = _ensure_aware(before_timestamp)
    start = before_timestamp - timedelta(minutes=lookback_minutes)

    def query(active_session: Session) -> bool:
        return (
            active_session.scalar(
                select(NewsItem.id)
                .where(
                    NewsItem.topic == topic,
                    NewsItem.published_at >= start,
                    NewsItem.published_at <= before_timestamp,
                )
                .limit(1)
            )
            is not None
        )

    if session is not None:
        return query(session)
    with session_scope() as active_session:
        return query(active_session)


def get_headline_timeline(
    topic: str,
    start: datetime,
    end: datetime,
    session: Session | None = None,
) -> list[NewsItem]:
    start = _ensure_aware(start)
    end = _ensure_aware(end)

    def query(active_session: Session) -> list[NewsItem]:
        return list(
            active_session.scalars(
                select(NewsItem)
                .where(
                    NewsItem.topic == topic,
                    NewsItem.published_at >= start,
                    NewsItem.published_at <= end,
                )
                .order_by(NewsItem.published_at.asc())
            )
        )

    if session is not None:
        return query(session)
    with session_scope() as active_session:
        return query(active_session)

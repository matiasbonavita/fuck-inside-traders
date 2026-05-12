from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from fuck_inside_traders.config import (
    PolymarketWatchlistEntry,
    Thresholds,
    Topic,
    load_polymarket_watchlist,
    load_thresholds,
    load_topics,
)
from fuck_inside_traders.provenance import LIVE, MOCK, SYNTHETIC, UNKNOWN
from fuck_inside_traders.settings import get_settings
from fuck_inside_traders.storage.models import (
    CollectorStatus,
    Market,
    PolymarketDiscoveryCandidate,
    PredictionMarketSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PredictionMarketRecord:
    source: str
    provider_kind: str
    external_id: str
    title: str
    topic: str
    url: str | None
    active: bool
    probability: float
    volume: float | None
    liquidity: float | None
    bid: float | None
    ask: float | None
    timestamp: datetime
    slug: str | None = None
    relevance_score: float = 1.0


@dataclass(frozen=True)
class MarketRelevanceResult:
    score: float
    reason: str
    matched_keywords: tuple[str, ...] = ()
    negative_matches: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolymarketDiscoveryRecord:
    topic: str
    query: str | None
    source: str
    external_id: str | None
    slug: str | None
    title: str
    url: str | None
    active: bool
    closed: bool
    accepted: bool
    relevance_score: float
    rejection_reason: str | None
    provider_kind: str
    raw_payload_json: dict[str, Any] | None


class PredictionMarketProvider(Protocol):
    def fetch_markets(self, topics: list[Topic]) -> list[PredictionMarketRecord]:
        pass


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _as_float(value: Any) -> float | None:
    value = _parse_jsonish(value)
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    value = _parse_jsonish(value)
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    return str(value)


def _keywords_in_text(keywords: list[str], text: str) -> set[str]:
    lower_text = text.lower()
    return {keyword.lower() for keyword in keywords if keyword.lower() in lower_text}


def _relevant_threshold(topic: Topic, thresholds: Thresholds) -> float:
    return max(topic.min_market_relevance_score, thresholds.polymarket_relevance_threshold)


def score_polymarket_relevance(
    raw_market: dict[str, Any] | str,
    topic: Topic,
    thresholds: Thresholds | None = None,
) -> MarketRelevanceResult:
    thresholds = thresholds or load_thresholds()
    if isinstance(raw_market, str):
        raw_market = {"question": raw_market}

    title = _as_text(
        raw_market.get("question")
        or raw_market.get("title")
        or raw_market.get("_event_title")
    )
    description = _as_text(raw_market.get("description") or raw_market.get("_event_description"))
    slug = _as_text(raw_market.get("slug") or raw_market.get("_event_slug"))
    category = _as_text(raw_market.get("category") or raw_market.get("_event_category"))
    tags = _as_text(raw_market.get("tags") or raw_market.get("_event_tags"))
    outcomes = _as_text(raw_market.get("outcomes"))

    title_text = title.lower()
    context_text = " ".join([description, slug, category, tags, outcomes]).lower()
    high_signal_text = " ".join([title, slug, category, tags]).lower()

    negative_keywords = [
        *topic.exclude_keywords,
        *thresholds.polymarket_negative_keywords,
    ]
    negative_matches = sorted(_keywords_in_text(negative_keywords, high_signal_text))
    if negative_matches:
        return MarketRelevanceResult(
            score=0.0,
            reason=f"negative_keyword:{','.join(negative_matches[:3])}",
            negative_matches=tuple(negative_matches),
        )

    topic_keywords = topic.include_keywords or topic.keywords
    title_matches = sorted(_keywords_in_text(topic_keywords, title_text))
    context_matches = sorted(_keywords_in_text(topic_keywords, context_text))
    relevant_category_matches = sorted(
        _keywords_in_text(thresholds.polymarket_relevant_categories, category.lower())
        | _keywords_in_text(thresholds.polymarket_relevant_categories, tags.lower())
    )

    score = 0.0
    score += min(0.75, len(title_matches) * 0.25)
    context_only_matches = set(context_matches) - set(title_matches)
    score += min(0.30, len(context_only_matches) * 0.08)
    if relevant_category_matches:
        score += 0.15
    if len(title_matches) >= 2:
        score += 0.10

    score = min(1.0, score)
    all_matches = tuple(sorted(set(title_matches) | set(context_matches)))
    if not all_matches:
        return MarketRelevanceResult(score=0.0, reason="no_topic_keyword_match")

    threshold = _relevant_threshold(topic, thresholds)
    if score < threshold:
        return MarketRelevanceResult(
            score=score,
            reason=f"score_below_threshold:{score:.2f}<{threshold:.2f}",
            matched_keywords=all_matches,
        )

    return MarketRelevanceResult(
        score=score,
        reason=f"accepted:keywords={','.join(all_matches[:5])}",
        matched_keywords=all_matches,
    )


def market_relevance_score(
    title: str,
    topic: Topic,
    thresholds: Thresholds | None = None,
) -> float:
    return score_polymarket_relevance(title, topic, thresholds).score


def _normalized_identifiers(record: PredictionMarketRecord) -> set[str]:
    values = {
        record.external_id,
        record.slug,
        record.url,
        record.title,
    }
    return {str(value).strip().lower() for value in values if value}


def _is_listed(record: PredictionMarketRecord, configured_values: list[str]) -> bool:
    configured = {value.strip().lower() for value in configured_values if value.strip()}
    return bool(configured & _normalized_identifiers(record))


def _topic_queries(topic: Topic) -> list[str]:
    if topic.polymarket_queries:
        return topic.polymarket_queries
    search_terms = topic.include_keywords or topic.keywords
    return [" ".join(search_terms[:4])]


def _slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    for marker in ("event", "market"):
        if marker in parts:
            index = parts.index(marker)
            if len(parts) > index + 1:
                return parts[index + 1]
    return parts[-1] if parts else None


def _is_closed(raw_market: dict[str, Any]) -> bool:
    return bool(raw_market.get("closed") or raw_market.get("_event_closed"))


def _is_active(raw_market: dict[str, Any]) -> bool:
    return bool(raw_market.get("active", raw_market.get("_event_active", True)))


class PolymarketHttpProvider:
    source = "polymarket"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 12.0,
        transport: httpx.BaseTransport | None = None,
        watchlist: dict[str, list[PolymarketWatchlistEntry]] | None = None,
        thresholds: Thresholds | None = None,
    ) -> None:
        self.base_url = base_url or get_settings().polymarket_api_base_url
        self.timeout = timeout
        self.transport = transport
        self.watchlist = watchlist if watchlist is not None else load_polymarket_watchlist()
        self.thresholds = thresholds or load_thresholds()
        self.discovery_candidates: list[PolymarketDiscoveryRecord] = []

    def fetch_markets(self, topics: list[Topic]) -> list[PredictionMarketRecord]:
        records_by_external_id: dict[str, PredictionMarketRecord] = {}
        self.discovery_candidates = []
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            for topic in topics:
                watchlist_records = self._fetch_watchlist_records(client, topic)
                if watchlist_records:
                    for record in watchlist_records:
                        records_by_external_id[record.external_id] = record
                    logger.info(
                        "Polymarket watchlist accepted topic=%s count=%s",
                        topic.name,
                        len(watchlist_records),
                    )
                    continue

                for query in _topic_queries(topic):
                    endpoint = f"{self.base_url}/public-search"
                    try:
                        response = client.get(
                            endpoint,
                            params={
                                "q": query,
                                "limit": 25,
                            },
                        )
                        response.raise_for_status()
                        payload = response.json()
                    except Exception as exc:
                        logger.exception(
                            "Prediction market fetch failed provider=%s endpoint=%s "
                            "topic=%s query=%s error=%s",
                            self.source,
                            endpoint,
                            topic.name,
                            query,
                            exc,
                        )
                        continue

                    raw_markets = self._extract_raw_markets(payload)
                    if not raw_markets:
                        logger.warning(
                            "Unexpected Polymarket response shape endpoint=%s topic=%s query=%s",
                            endpoint,
                            topic.name,
                            query,
                        )
                        continue

                    rejected_count = 0
                    rejected_examples: list[str] = []
                    accepted_records: list[PredictionMarketRecord] = []
                    for raw_market in raw_markets:
                        if not isinstance(raw_market, dict):
                            continue
                        record = self._review_raw_market(
                            raw_market,
                            topic,
                            query=query,
                            watchlisted=False,
                        )
                        if record is None:
                            rejected_count += 1
                            if len(rejected_examples) < 5:
                                rejected_examples.append(self._candidate_title(raw_market))
                        else:
                            accepted_records.append(record)
                            records_by_external_id[record.external_id] = record
                    logger.info(
                        "Polymarket query reviewed topic=%s query=%s accepted=%s "
                        "rejected=%s examples=%s",
                        topic.name,
                        query,
                        len(accepted_records),
                        rejected_count,
                        rejected_examples,
                    )
        return list(records_by_external_id.values())

    def _fetch_watchlist_records(
        self,
        client: httpx.Client,
        topic: Topic,
    ) -> list[PredictionMarketRecord]:
        entries = self.watchlist.get(topic.name, [])
        if not entries:
            return []

        records: list[PredictionMarketRecord] = []
        for entry in entries:
            raw_markets = self._fetch_watchlist_entry(client, topic, entry)
            if not raw_markets:
                self.discovery_candidates.append(
                    PolymarketDiscoveryRecord(
                        topic=topic.name,
                        query="watchlist",
                        source=self.source,
                        external_id=entry.external_id,
                        slug=entry.slug or _slug_from_url(entry.url),
                        title=entry.description
                        or entry.slug
                        or entry.external_id
                        or "Unfetchable watchlist entry",
                        url=entry.url,
                        active=entry.active,
                        closed=False,
                        accepted=False,
                        relevance_score=0.0,
                        rejection_reason="watchlist_fetch_failed",
                        provider_kind=LIVE,
                        raw_payload_json={
                            "slug": entry.slug,
                            "external_id": entry.external_id,
                            "url": entry.url,
                            "description": entry.description,
                            "active": entry.active,
                        },
                    )
                )
                logger.warning(
                    "Polymarket watchlist entry could not be fetched topic=%s slug=%s "
                    "external_id=%s url=%s",
                    topic.name,
                    entry.slug,
                    entry.external_id,
                    entry.url,
                )
                continue

            for raw_market in raw_markets:
                record = self._review_raw_market(
                    raw_market,
                    topic,
                    query="watchlist",
                    watchlisted=True,
                    expected_active=entry.active,
                )
                if record is not None:
                    records.append(record)
        return records

    def _fetch_watchlist_entry(
        self,
        client: httpx.Client,
        topic: Topic,
        entry: PolymarketWatchlistEntry,
    ) -> list[dict[str, Any]]:
        slug = entry.slug or _slug_from_url(entry.url)
        requests: list[tuple[str, dict[str, str]]] = []
        if entry.external_id:
            requests.extend(
                [
                    (f"{self.base_url}/markets/{entry.external_id}", {}),
                    (f"{self.base_url}/markets", {"id": entry.external_id}),
                    (f"{self.base_url}/markets", {"condition_ids": entry.external_id}),
                ]
            )
        if slug:
            if entry.external_id:
                requests.extend(
                    [
                        (f"{self.base_url}/markets/slug/{slug}", {}),
                        (f"{self.base_url}/events/slug/{slug}", {}),
                        (f"{self.base_url}/markets", {"slug": slug}),
                    ]
                )
            else:
                requests.extend(
                    [
                        (f"{self.base_url}/events/slug/{slug}", {}),
                        (f"{self.base_url}/markets/slug/{slug}", {}),
                        (f"{self.base_url}/markets", {"slug": slug}),
                    ]
                )

        if not requests:
            logger.warning("Polymarket watchlist entry has no identifier topic=%s", topic.name)
            return []

        for endpoint, params in requests:
            try:
                response = client.get(endpoint, params=params)
                if response.status_code in {404, 422}:
                    continue
                response.raise_for_status()
                raw_markets = self._extract_raw_markets(response.json())
            except Exception as exc:
                logger.warning(
                    "Polymarket watchlist fetch failed endpoint=%s topic=%s error=%s",
                    endpoint,
                    topic.name,
                    exc,
                )
                continue
            if raw_markets:
                return raw_markets
        return []

    def _review_raw_market(
        self,
        raw_market: dict[str, Any],
        topic: Topic,
        *,
        query: str | None,
        watchlisted: bool,
        expected_active: bool = True,
    ) -> PredictionMarketRecord | None:
        candidate_title = self._candidate_title(raw_market)
        external_id = self._candidate_external_id(raw_market)
        slug = self._candidate_slug(raw_market)
        url = self._candidate_url(raw_market)
        active = _is_active(raw_market)
        closed = _is_closed(raw_market)

        rejection_reason: str | None = None
        relevance = score_polymarket_relevance(raw_market, topic, self.thresholds)
        relevance_score = relevance.score
        accepted = False

        draft_record = PredictionMarketRecord(
            source=self.source,
            provider_kind=LIVE,
            external_id=external_id or "",
            title=candidate_title,
            topic=topic.name,
            url=url,
            active=active,
            probability=0.0,
            volume=None,
            liquidity=None,
            bid=None,
            ask=None,
            timestamp=datetime.now(UTC),
            slug=slug,
            relevance_score=relevance_score,
        )
        if _is_listed(draft_record, topic.polymarket_blocklist):
            rejection_reason = "blocklisted"
        elif not expected_active:
            rejection_reason = "watchlist_entry_inactive"
        elif not active:
            rejection_reason = "inactive"
        elif closed:
            rejection_reason = "closed"
        else:
            is_allowlisted = _is_listed(draft_record, topic.polymarket_allowlist)
            if watchlisted or is_allowlisted:
                accepted = True
                relevance_score = max(relevance_score, _relevant_threshold(topic, self.thresholds))
                rejection_reason = None
            elif relevance.score >= _relevant_threshold(topic, self.thresholds):
                accepted = True
            else:
                rejection_reason = relevance.reason

        record = self._normalize_market(raw_market, topic, relevance_score)
        if accepted and record is None:
            accepted = False
            rejection_reason = "missing_probability_or_identifier"

        self.discovery_candidates.append(
            PolymarketDiscoveryRecord(
                topic=topic.name,
                query=query,
                source=self.source,
                external_id=external_id,
                slug=slug,
                title=candidate_title,
                url=url,
                active=active,
                closed=closed,
                accepted=accepted,
                relevance_score=relevance_score,
                rejection_reason=rejection_reason,
                provider_kind=LIVE,
                raw_payload_json=self._compact_raw_payload(raw_market),
            )
        )

        if accepted and record is not None:
            return replace(record, relevance_score=relevance_score)
        return None

    def _extract_raw_markets(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []

        raw_markets: list[dict[str, Any]] = []
        events = payload.get("events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_markets = event.get("markets")
                if isinstance(event_markets, list) and event_markets:
                    for market in event_markets:
                        if isinstance(market, dict):
                            raw_markets.append(self._merge_event_context(event, market))
                else:
                    raw_markets.append(event)
        markets = payload.get("markets")
        if isinstance(markets, list):
            raw_markets.extend(item for item in markets if isinstance(item, dict))
        if not raw_markets and ("question" in payload or "title" in payload or "slug" in payload):
            raw_markets.append(payload)
        return raw_markets

    def _merge_event_context(
        self,
        event: dict[str, Any],
        market: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(market)
        merged["_event_id"] = event.get("id")
        merged["_event_title"] = event.get("title")
        merged["_event_description"] = event.get("description")
        merged["_event_slug"] = event.get("slug")
        merged["_event_category"] = event.get("category") or event.get("subcategory")
        merged["_event_tags"] = event.get("tags")
        merged["_event_active"] = event.get("active")
        merged["_event_closed"] = event.get("closed")
        return merged

    def _candidate_title(self, raw_market: dict[str, Any]) -> str:
        return str(
            raw_market.get("question")
            or raw_market.get("title")
            or raw_market.get("_event_title")
            or "Untitled Polymarket candidate"
        ).strip()

    def _candidate_external_id(self, raw_market: dict[str, Any]) -> str | None:
        value = raw_market.get("id") or raw_market.get("conditionId") or raw_market.get("_event_id")
        return str(value).strip() if value else None

    def _candidate_slug(self, raw_market: dict[str, Any]) -> str | None:
        value = raw_market.get("slug") or raw_market.get("_event_slug")
        return str(value).strip() if value else None

    def _candidate_url(self, raw_market: dict[str, Any]) -> str | None:
        url = raw_market.get("url")
        if url:
            return str(url)
        slug = self._candidate_slug(raw_market)
        return f"https://polymarket.com/event/{slug}" if slug else None

    def _compact_raw_payload(self, raw_market: dict[str, Any]) -> dict[str, Any]:
        keys = [
            "id",
            "conditionId",
            "slug",
            "question",
            "title",
            "description",
            "category",
            "tags",
            "outcomes",
            "active",
            "closed",
            "volume",
            "liquidity",
            "bestBid",
            "bestAsk",
            "outcomePrices",
            "_event_title",
            "_event_slug",
            "_event_category",
            "_event_active",
            "_event_closed",
        ]
        return {key: raw_market.get(key) for key in keys if key in raw_market}

    def _normalize_market(
        self,
        raw_market: dict[str, Any],
        topic: Topic,
        relevance_score: float = 1.0,
    ) -> PredictionMarketRecord | None:
        title = self._candidate_title(raw_market)
        external_id = self._candidate_external_id(raw_market)
        if not title or not external_id:
            return None

        probability = (
            _as_float(raw_market.get("probability"))
            or _as_float(raw_market.get("lastTradePrice"))
            or _as_float(raw_market.get("outcomePrices"))
            or _as_float(raw_market.get("bestAsk"))
        )
        if probability is None:
            return None

        slug = self._candidate_slug(raw_market)
        url = self._candidate_url(raw_market)
        now = datetime.now(UTC)
        return PredictionMarketRecord(
            source=self.source,
            provider_kind=LIVE,
            external_id=str(external_id),
            title=title,
            topic=topic.name,
            url=str(url) if url else None,
            active=_is_active(raw_market),
            probability=max(0.0, min(1.0, probability)),
            volume=_as_float(raw_market.get("volume") or raw_market.get("volumeNum")),
            liquidity=_as_float(raw_market.get("liquidity") or raw_market.get("liquidityNum")),
            bid=_as_float(raw_market.get("bestBid") or raw_market.get("bid")),
            ask=_as_float(raw_market.get("bestAsk") or raw_market.get("ask")),
            timestamp=now,
            slug=str(slug) if slug else None,
            relevance_score=relevance_score,
        )


class FallbackPredictionMarketProvider:
    source = "polymarket_mock"

    def fetch_markets(self, topics: list[Topic]) -> list[PredictionMarketRecord]:
        now = datetime.now(UTC).replace(microsecond=0)
        records = []
        for topic in topics:
            records.append(
                PredictionMarketRecord(
                    source=self.source,
                    provider_kind=MOCK,
                    external_id=f"mock-{topic.name}-oil-shock",
                    title=f"Will {topic.name.replace('_', ' ')} disrupt energy markets this week?",
                    topic=topic.name,
                    url="https://example.local/polymarket/mock",
                    active=True,
                    probability=0.63,
                    volume=42_000.0,
                    liquidity=18_000.0,
                    bid=0.62,
                    ask=0.64,
                    timestamp=now,
                )
            )
        return records


class PolymarketCollector:
    def __init__(
        self,
        provider: PredictionMarketProvider | None = None,
        fallback_provider: PredictionMarketProvider | None = None,
    ) -> None:
        self.provider = provider or PolymarketHttpProvider()
        self.fallback_provider = fallback_provider or FallbackPredictionMarketProvider()

    def collect(self, session: Session, topics: list[Topic] | None = None) -> int:
        topics = topics or load_topics()
        records = self.provider.fetch_markets(topics)
        discovery_candidates = list(getattr(self.provider, "discovery_candidates", []))
        self._store_discovery_candidates(session, discovery_candidates)
        used_fallback = False
        fallback_attempted = False
        if not records:
            summary = self._discovery_summary(discovery_candidates)
            logger.warning(
                "No live prediction market records found; using fallback provider "
                "candidates_fetched=%s accepted=%s rejected=%s top_rejection_reasons=%s",
                summary["fetched"],
                summary["accepted"],
                summary["rejected"],
                summary["top_rejection_reasons"],
            )
            records = self.fallback_provider.fetch_markets(topics)
            fallback_attempted = True
            used_fallback = True
        if any(record.provider_kind != LIVE for record in records):
            used_fallback = True

        count = 0
        for record in records:
            market = self._upsert_market(session, record)
            self._seed_first_run_baseline(session, market, record)
            snapshot_volume = record.volume if record.volume is not None else 1000.0
            session.add(
                PredictionMarketSnapshot(
                    market=market,
                    probability=record.probability,
                    volume=snapshot_volume,
                    liquidity=record.liquidity,
                    bid=record.bid,
                    ask=record.ask,
                    timestamp=record.timestamp,
                    source=record.source,
                    provider_kind=record.provider_kind,
                )
            )
            count += 1
        self._deactivate_absent_live_markets(session, records)
        summary = self._discovery_summary(discovery_candidates)
        status = "ok"
        provider_kind = LIVE
        if used_fallback:
            status = "mock_backed" if count else "no_live_candidates"
            provider_kind = MOCK if count else UNKNOWN
        message = (
            f"Collected prediction market snapshots count={count}; "
            f"live_candidates_fetched={summary['fetched']} accepted={summary['accepted']} "
            f"rejected={summary['rejected']} "
            f"top_rejection_reasons={summary['top_rejection_reasons']} "
            f"fallback_attempted={fallback_attempted} mock_data_used={used_fallback and count > 0}"
        )
        session.add(
            CollectorStatus(
                provider="polymarket",
                data_type="prediction_market",
                status=status,
                provider_kind=provider_kind,
                message=message,
            )
        )
        logger.info(message)
        return count

    def _deactivate_absent_live_markets(
        self,
        session: Session,
        records: list[PredictionMarketRecord],
    ) -> None:
        live_records_by_source_topic: dict[tuple[str, str], set[str]] = {}
        for record in records:
            if record.provider_kind != LIVE:
                continue
            live_records_by_source_topic.setdefault((record.source, record.topic), set()).add(
                record.external_id
            )

        for (source, topic), external_ids in live_records_by_source_topic.items():
            inactive_markets = list(
                session.scalars(
                    select(Market).where(
                        Market.source == source,
                        Market.topic == topic,
                        Market.active.is_(True),
                        ~Market.external_id.in_(external_ids),
                    )
                )
            )
            for market in inactive_markets:
                market.active = False
            if inactive_markets:
                logger.info(
                    "Deactivated absent prediction markets source=%s topic=%s count=%s",
                    source,
                    topic,
                    len(inactive_markets),
                )

    def _store_discovery_candidates(
        self,
        session: Session,
        candidates: list[PolymarketDiscoveryRecord],
    ) -> None:
        for candidate in candidates:
            session.add(
                PolymarketDiscoveryCandidate(
                    topic=candidate.topic,
                    query=candidate.query,
                    source=candidate.source,
                    external_id=candidate.external_id,
                    slug=candidate.slug,
                    title=candidate.title,
                    url=candidate.url,
                    active=candidate.active,
                    closed=candidate.closed,
                    accepted=candidate.accepted,
                    relevance_score=candidate.relevance_score,
                    rejection_reason=candidate.rejection_reason,
                    provider_kind=candidate.provider_kind,
                    raw_payload_json=candidate.raw_payload_json,
                )
            )

    def _discovery_summary(
        self,
        candidates: list[PolymarketDiscoveryRecord],
    ) -> dict[str, Any]:
        rejected_reasons = Counter(
            candidate.rejection_reason or "unknown"
            for candidate in candidates
            if not candidate.accepted
        )
        return {
            "fetched": len(candidates),
            "accepted": sum(1 for candidate in candidates if candidate.accepted),
            "rejected": sum(1 for candidate in candidates if not candidate.accepted),
            "top_rejection_reasons": dict(rejected_reasons.most_common(5)),
        }

    def _upsert_market(self, session: Session, record: PredictionMarketRecord) -> Market:
        market = session.scalar(
            select(Market).where(
                Market.source == record.source,
                Market.external_id == record.external_id,
            )
        )
        if market is None:
            market = Market(
                source=record.source,
                external_id=record.external_id,
                title=record.title,
                topic=record.topic,
                url=record.url,
                active=record.active,
            )
            session.add(market)
            session.flush()
        else:
            market.title = record.title
            market.topic = record.topic
            market.url = record.url
            market.active = record.active
        return market

    def _seed_first_run_baseline(
        self,
        session: Session,
        market: Market,
        record: PredictionMarketRecord,
    ) -> None:
        existing_count = session.scalar(
            select(PredictionMarketSnapshot.id)
            .where(PredictionMarketSnapshot.market_id == market.id)
            .limit(1)
        )
        if existing_count is not None:
            return

        baseline_probability = max(0.01, record.probability - 0.22)
        baseline_volume = max(1.0, (record.volume or 1000.0) * 0.35)
        session.add(
            PredictionMarketSnapshot(
                market=market,
                probability=baseline_probability,
                volume=baseline_volume,
                liquidity=record.liquidity,
                bid=max(0.01, baseline_probability - 0.01),
                ask=min(0.99, baseline_probability + 0.01),
                timestamp=record.timestamp - timedelta(minutes=30),
                source=f"{record.source}_synthetic_baseline",
                provider_kind=SYNTHETIC,
            )
        )

from __future__ import annotations

import logging
from typing import Any

import httpx

from fuck_inside_traders.provenance import (
    ALERT_MOCK_BACKED,
    alert_label_description,
)
from fuck_inside_traders.reports.analyst import summarize_anomaly_event
from fuck_inside_traders.settings import get_settings
from fuck_inside_traders.storage.models import AnomalyEvent, Market

logger = logging.getLogger(__name__)


def format_alert(event: AnomalyEvent, market: Market | None = None) -> str:
    explanation: dict[str, Any] = event.explanation_json or {}
    scores = explanation.get("scores", {})
    asset_moves = explanation.get("asset_moves", {})
    timeline = explanation.get("headline_timeline", [])
    provenance = explanation.get("provenance", {})
    alert_label = provenance.get("alert_label", "PARTIAL-LIVE")
    components = provenance.get("components", {})
    market_title = explanation.get("market_title") or (market.title if market else "Unknown market")
    asset_summary = ", ".join(
        f"{symbol} {move:+.2%}" for symbol, move in sorted(asset_moves.items())
    ) or "none"
    timeline_summary = "\n".join(
        f"- {item.get('published_at', 'unknown')}: {item.get('title', 'Untitled')}"
        for item in timeline[:5]
    ) or "- No public headline in timeline window"
    provenance_summary = "\n".join(
        f"- {name}: {', '.join(component.get('provider_kinds', ['unknown']))} "
        f"via {', '.join(component.get('sources', ['unknown']))}"
        for name, component in sorted(components.items())
    ) or "- provenance unavailable"
    mock_notice = ""
    if alert_label == ALERT_MOCK_BACKED:
        mock_notice = (
            "\nDemo-only notice: core movement depends on mock, fallback, or synthetic data. "
            "Do not treat this as a real anomaly candidate.\n"
        )

    return (
        "DRY-RUN anomaly alert\n"
        f"Provenance label: {alert_label}\n"
        f"Provenance meaning: {alert_label_description(alert_label)}"
        f"{mock_notice}\n"
        f"Topic: {event.topic}\n"
        f"Market: {market_title}\n"
        f"Odds jump: {event.odds_jump:+.2%} "
        f"(score {float(scores.get('odds_jump', 0.0)):.2f})\n"
        f"Volume z-score: {event.volume_z_score:.2f} "
        f"(score {float(scores.get('volume_spike', 0.0)):.2f})\n"
        f"Related asset confirmation: {event.asset_confirmation_score:.2f} "
        f"({asset_summary})\n"
        f"Headline gap: {event.headline_gap_minutes:.0f} minutes "
        f"(score {float(scores.get('headline_gap', 0.0)):.2f})\n"
        f"Signal score: {event.signal_score:.2f}\n"
        f"Signal provenance:\n{provenance_summary}\n"
        f"Timeline:\n{timeline_summary}\n"
        f"Analyst note: {summarize_anomaly_event(event, market)}"
    )


class TelegramAlerter:
    def __init__(self) -> None:
        self.settings = get_settings()

    def send(self, event: AnomalyEvent, market: Market | None = None) -> bool:
        message = format_alert(event, market)
        if self.settings.dry_run:
            logger.info("Dry-run Telegram alert:\n%s", message)
            return True

        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            logger.warning("Telegram credentials missing; alert not sent")
            return False

        endpoint = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        try:
            response = httpx.post(
                endpoint,
                json={"chat_id": self.settings.telegram_chat_id, "text": message},
                timeout=12.0,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.exception("Telegram send failed endpoint=%s error=%s", endpoint, exc)
            return False

        logger.info("Telegram alert sent event_id=%s", event.id)
        return True

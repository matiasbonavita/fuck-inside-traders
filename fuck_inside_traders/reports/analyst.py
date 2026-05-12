from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from fuck_inside_traders.reports.schemas import AnalystContext, AnalystReportResult
from fuck_inside_traders.settings import Settings, get_settings
from fuck_inside_traders.storage.models import AnomalyEvent, Market

logger = logging.getLogger(__name__)

DETERMINISTIC_BACKEND = "deterministic"
HERMES_BACKEND = "hermes"


class AnalystBackend(Protocol):
    name: str

    def analyze(self, context: AnalystContext) -> AnalystReportResult:
        pass


def _headline_sentence(context: AnalystContext) -> str:
    if context.event.headline_gap_minutes >= 1 and context.scores.headline_gap > 0:
        return "Headline timing was included in the deterministic score."
    return "No strong pre-move public headline gap was available in the context."


class DeterministicAnalystBackend:
    name = DETERMINISTIC_BACKEND

    def analyze(self, context: AnalystContext) -> AnalystReportResult:
        market_title = context.market.title if context.market else "Unknown market"
        summary = (
            f"Anomaly radar alert for topic `{context.event.topic}`. "
            f"Market: {market_title}. "
            f"Signal score: {context.event.signal_score:.2f}. "
            f"Provenance label: {context.provenance.alert_label}. "
            f"Odds moved {context.event.odds_jump:+.2%}; "
            f"volume z-score was {context.event.volume_z_score:.2f}; "
            f"related asset confirmation score was "
            f"{context.event.asset_confirmation_score:.2f}. "
            f"{_headline_sentence(context)} "
            f"Headline items in timeline: {len(context.headline_timeline)}. "
            "This is a dry-run public-data anomaly report for manual review, "
            "not an accusation."
        )
        return AnalystReportResult(
            backend=self.name,
            status="success",
            summary_text=summary,
            report_json={
                "schema_version": "analyst_report.v1",
                "backend": self.name,
                "status": "success",
                "provenance_label": context.provenance.alert_label,
                "scores": context.scores.model_dump(mode="json"),
                "counts": {
                    "prediction_snapshots": len(context.prediction_snapshots),
                    "asset_snapshots": len(context.asset_snapshots),
                    "headlines": len(context.headline_timeline),
                    "provider_statuses": len(context.provider_statuses),
                },
                "key_points": [
                    "Python detector already created this AnomalyEvent.",
                    "This report summarizes public data for manual review.",
                    "No accusation, suspect identification, or trade instruction is made.",
                ],
                "safety_notes": context.safety_notes,
            },
        )


class HermesAnalystBackend:
    name = HERMES_BACKEND

    def __init__(
        self,
        *,
        enabled: bool,
        endpoint: str | None,
        timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.enabled = enabled
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.client = client

    def build_payload(self, context: AnalystContext) -> dict[str, Any]:
        return {
            "schema_version": "hermes_anomaly_context.v1",
            "instructions": {
                "role": "optional_analyst_reporting_layer",
                "detector_source_of_truth": "deterministic_python",
                "may": [
                    "summarize public-data context",
                    "classify likely event theme",
                    "write a human-readable timeline",
                    "suggest what to inspect next",
                ],
                "must_not": [
                    "decide whether an anomaly exists",
                    "override deterministic scoring",
                    "execute trades",
                    "choose position sizes",
                    "identify suspects",
                    "make accusations of insider trading",
                ],
            },
            "context": context.model_dump(mode="json"),
        }

    def analyze(self, context: AnalystContext) -> AnalystReportResult:
        payload = self.build_payload(context)
        if not self.enabled:
            logger.info("Hermes analyst backend disabled; report marked disabled")
            return AnalystReportResult(
                backend=self.name,
                status="disabled",
                summary_text=(
                    "Hermes analyst backend is disabled. FIT remains on deterministic "
                    "local reporting; no Hermes network call was made."
                ),
                report_json={
                    "schema_version": "analyst_report.v1",
                    "backend": self.name,
                    "status": "disabled",
                    "payload_validated": True,
                    "payload_preview": {
                        "schema_version": payload["schema_version"],
                        "event_id": context.event.id,
                        "provenance_label": context.provenance.alert_label,
                    },
                    "safety_notes": context.safety_notes,
                },
            )
        if not self.endpoint:
            logger.warning("Hermes analyst backend enabled without endpoint; report marked failed")
            return AnalystReportResult(
                backend=self.name,
                status="failed",
                summary_text=(
                    "Hermes analyst backend is enabled but HERMES_ENDPOINT is not configured. "
                    "No Hermes network call was made."
                ),
                report_json={
                    "schema_version": "analyst_report.v1",
                    "backend": self.name,
                    "status": "failed",
                    "payload_validated": True,
                    "error": "missing_hermes_endpoint",
                    "safety_notes": context.safety_notes,
                },
            )

        try:
            if self.client is not None:
                response = self.client.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(self.endpoint, json=payload)
            response.raise_for_status()
            hermes_response = response.json()
        except Exception as exc:
            logger.exception("Hermes analyst call failed endpoint=%s error=%s", self.endpoint, exc)
            return AnalystReportResult(
                backend=self.name,
                status="failed",
                summary_text=f"Hermes analyst call failed: {exc}",
                report_json={
                    "schema_version": "analyst_report.v1",
                    "backend": self.name,
                    "status": "failed",
                    "error": str(exc),
                    "safety_notes": context.safety_notes,
                },
            )

        summary = str(
            hermes_response.get("summary_text")
            or hermes_response.get("summary")
            or "Hermes returned a report without summary text."
        )
        return AnalystReportResult(
            backend=self.name,
            status="success",
            summary_text=summary,
            report_json={
                "schema_version": "analyst_report.v1",
                "backend": self.name,
                "status": "success",
                "hermes_response": hermes_response,
                "safety_notes": context.safety_notes,
            },
        )


def get_analyst_backend(settings: Settings | None = None) -> AnalystBackend:
    settings = settings or get_settings()
    backend_name = settings.analyst_backend.strip().lower()
    if backend_name == HERMES_BACKEND:
        return HermesAnalystBackend(
            enabled=settings.hermes_enabled,
            endpoint=settings.hermes_endpoint,
            timeout_seconds=settings.hermes_timeout_seconds,
        )
    if backend_name != DETERMINISTIC_BACKEND:
        logger.warning(
            "Unknown ANALYST_BACKEND=%s; falling back to deterministic analyst backend",
            settings.analyst_backend,
        )
    return DeterministicAnalystBackend()


def summarize_anomaly_event(event: AnomalyEvent, market: Market | None = None) -> str:
    """Compatibility helper used by dry-run alerts."""
    explanation = event.explanation_json or {}
    market_title = explanation.get("market_title") or (market.title if market else "Unknown market")
    timeline = explanation.get("headline_timeline") or []
    provenance = explanation.get("provenance", {})
    alert_label = provenance.get("alert_label", "PARTIAL-LIVE")
    headline_text = "Headline timing was included in the deterministic score."
    if not explanation.get("had_headline_before"):
        headline_text = (
            "No matching public headline was found before the move in the configured lookback."
        )

    return (
        f"Anomaly radar alert for topic `{event.topic}`. "
        f"Market: {market_title}. "
        f"Signal score: {event.signal_score:.2f}. "
        f"Provenance label: {alert_label}. "
        f"Odds moved {event.odds_jump:+.2%}; volume z-score was {event.volume_z_score:.2f}; "
        f"related asset confirmation score was {event.asset_confirmation_score:.2f}. "
        f"{headline_text} "
        f"Headline items in timeline: {len(timeline)}. "
        "This is a dry-run public-data anomaly report for manual review, not an accusation."
    )

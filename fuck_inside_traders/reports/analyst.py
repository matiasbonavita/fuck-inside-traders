from __future__ import annotations

from fuck_inside_traders.storage.models import AnomalyEvent, Market


def summarize_anomaly_event(event: AnomalyEvent, market: Market | None = None) -> str:
    """Deterministic analyst summary stub for future optional Hermes integration."""
    explanation = event.explanation_json or {}
    market_title = explanation.get("market_title") or (market.title if market else "Unknown market")
    timeline = explanation.get("headline_timeline") or []
    provenance = explanation.get("provenance", {})
    alert_label = provenance.get("alert_label", "PARTIAL-LIVE")
    headline_text = (
        "No matching public headline was found before the move in the configured lookback."
    )
    if explanation.get("had_headline_before"):
        headline_text = (
            "A matching public headline existed before the move, reducing the gap score."
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
        "This is a dry-run market anomaly report for manual review, not an accusation."
    )

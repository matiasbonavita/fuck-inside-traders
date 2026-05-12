from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

LIVE = "live"
MOCK = "mock"
FALLBACK = "fallback"
SYNTHETIC = "synthetic"
UNKNOWN = "unknown"

ALERT_LIVE = "LIVE"
ALERT_PARTIAL_LIVE = "PARTIAL-LIVE"
ALERT_MOCK_BACKED = "MOCK-BACKED"
ALERT_UNKNOWN = "UNKNOWN"

NON_LIVE_KINDS = {MOCK, FALLBACK, SYNTHETIC, UNKNOWN}
MOCK_BACKING_KINDS = {MOCK, FALLBACK, SYNTHETIC}


@dataclass(frozen=True)
class ComponentProvenance:
    provider_kinds: list[str]
    sources: list[str]

    @property
    def has_live(self) -> bool:
        return LIVE in self.provider_kinds

    @property
    def has_non_live(self) -> bool:
        return any(kind in NON_LIVE_KINDS for kind in self.provider_kinds)

    @property
    def has_mock_backing(self) -> bool:
        return any(kind in MOCK_BACKING_KINDS for kind in self.provider_kinds)


def provider_kind_from_source(source: str | None) -> str:
    if not source:
        return UNKNOWN
    lowered = source.lower()
    if "synthetic" in lowered:
        return SYNTHETIC
    if "mock" in lowered:
        return MOCK
    if "fallback" in lowered:
        return FALLBACK
    return LIVE


def component_provenance(
    items: Iterable[Any],
    fallback_source: str | None = None,
) -> dict[str, Any]:
    kinds: set[str] = set()
    sources: set[str] = set()
    for item in items:
        source = getattr(item, "source", None) or fallback_source or UNKNOWN
        kind = getattr(item, "provider_kind", None) or provider_kind_from_source(source)
        sources.add(str(source))
        kinds.add(str(kind))

    if not kinds:
        kinds.add(UNKNOWN)
    if not sources:
        sources.add(fallback_source or UNKNOWN)

    return {
        "provider_kinds": sorted(kinds),
        "sources": sorted(sources),
    }


def alert_label_from_components(components: dict[str, dict[str, Any]]) -> str:
    core_components = ["odds_jump", "volume_spike", "asset_confirmation"]
    for component_name in core_components:
        component = components.get(component_name, {})
        if any(kind in MOCK_BACKING_KINDS for kind in component.get("provider_kinds", [])):
            return ALERT_MOCK_BACKED

    all_kinds = {
        kind
        for component in components.values()
        for kind in component.get("provider_kinds", [])
    }
    if not all_kinds or UNKNOWN in all_kinds:
        return ALERT_UNKNOWN
    if all_kinds == {LIVE}:
        return ALERT_LIVE
    return ALERT_PARTIAL_LIVE


def alert_label_description(label: str) -> str:
    if label == ALERT_LIVE:
        return "All required signal components used live provider data."
    if label == ALERT_PARTIAL_LIVE:
        return "At least one required component used live data and another was missing or fallback."
    if label == ALERT_UNKNOWN:
        return "This event predates provenance tracking or has incomplete source metadata."
    return "Core signal movement depends on mock, fallback, or synthetic data; demo only."


def anomaly_event_label(event: Any) -> str:
    explanation = event.explanation_json or {}
    return explanation.get("provenance", {}).get("alert_label") or ALERT_UNKNOWN


def is_demo_or_mock_event(event: Any) -> bool:
    return anomaly_event_label(event) in {ALERT_MOCK_BACKED, ALERT_UNKNOWN}

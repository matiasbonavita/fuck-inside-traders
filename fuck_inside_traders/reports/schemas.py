from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MarketContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None
    source: str
    external_id: str
    title: str
    topic: str
    url: str | None
    active: bool


class EventContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None
    topic: str
    signal_score: float
    odds_jump: float
    volume_z_score: float
    asset_confirmation_score: float
    headline_gap_minutes: float
    started_at: datetime
    ended_at: datetime
    created_at: datetime | None


class ScoreContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    odds_jump: float = 0.0
    volume_spike: float = 0.0
    asset_confirmation: float = 0.0
    headline_gap: float = 0.0
    topic_importance: float = 0.0


class PredictionSnapshotContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    probability: float
    volume: float | None
    liquidity: float | None
    bid: float | None
    ask: float | None
    source: str
    provider_kind: str


class AssetSnapshotContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    symbol: str
    topic: str
    price: float
    volume: float | None
    source: str
    provider_kind: str


class HeadlineContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    published_at: datetime
    source: str
    provider_kind: str
    title: str
    url: str | None


class ProviderStatusContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    provider: str
    data_type: str
    status: str
    provider_kind: str
    message: str | None


class ProvenanceComponentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_kinds: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class ProvenanceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_label: str
    components: dict[str, ProvenanceComponentContext] = Field(default_factory=dict)
    stale_reasons: list[str] = Field(default_factory=list)


class AnalystContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["analyst_context.v1"] = "analyst_context.v1"
    event: EventContext
    market: MarketContext | None
    scores: ScoreContext
    prediction_snapshots: list[PredictionSnapshotContext] = Field(default_factory=list)
    asset_snapshots: list[AssetSnapshotContext] = Field(default_factory=list)
    headline_timeline: list[HeadlineContext] = Field(default_factory=list)
    provider_statuses: list[ProviderStatusContext] = Field(default_factory=list)
    provenance: ProvenanceContext
    safety_notes: list[str] = Field(default_factory=list)


class AnalystReportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str
    status: Literal["success", "disabled", "failed"]
    summary_text: str
    report_json: dict[str, Any] = Field(default_factory=dict)

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from scipy.stats import norm

from fuck_inside_traders.time_utils import ensure_utc


@dataclass(frozen=True)
class OddsJumpResult:
    score: float
    odds_jump: float
    window_minutes: int
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True)
class VolumeSpikeResult:
    score: float
    z_score: float


@dataclass(frozen=True)
class AssetConfirmationResult:
    score: float
    moves: dict[str, float]


@dataclass(frozen=True)
class HeadlineGapResult:
    score: float
    gap_minutes: float
    had_headline_before: bool


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _timestamp(item: Any) -> datetime:
    return ensure_utc(item.timestamp)


def _latest(items: Sequence[Any]) -> Any | None:
    return max(items, key=_timestamp) if items else None


def _baseline_at_or_before(items: Sequence[Any], target: datetime) -> Any | None:
    eligible = [item for item in items if _timestamp(item) <= target]
    if eligible:
        return max(eligible, key=_timestamp)
    return min(items, key=_timestamp) if items else None


def compute_odds_jump(
    snapshots: Sequence[Any],
    windows_minutes: Iterable[int],
    full_score_jump: float = 0.20,
) -> OddsJumpResult:
    if len(snapshots) < 2:
        now = _timestamp(snapshots[0]) if snapshots else ensure_utc(datetime.now())
        return OddsJumpResult(
            score=0.0,
            odds_jump=0.0,
            window_minutes=0,
            started_at=now,
            ended_at=now,
        )

    ordered = sorted(snapshots, key=_timestamp)
    latest = ordered[-1]
    latest_time = _timestamp(latest)
    best = OddsJumpResult(
        score=0.0,
        odds_jump=0.0,
        window_minutes=0,
        started_at=_timestamp(ordered[0]),
        ended_at=latest_time,
    )

    for window in windows_minutes:
        baseline = _baseline_at_or_before(ordered, latest_time - timedelta(minutes=window))
        if baseline is None or baseline is latest:
            continue
        odds_jump = float(latest.probability) - float(baseline.probability)
        score = clamp(abs(odds_jump) / full_score_jump)
        if score > best.score:
            best = OddsJumpResult(
                score=score,
                odds_jump=odds_jump,
                window_minutes=int(window),
                started_at=_timestamp(baseline),
                ended_at=latest_time,
            )
    return best


def compute_volume_spike(snapshots: Sequence[Any]) -> VolumeSpikeResult:
    if len(snapshots) < 2:
        return VolumeSpikeResult(score=0.0, z_score=0.0)

    ordered = sorted(snapshots, key=_timestamp)
    latest_volume = ordered[-1].volume
    baseline_values = [snapshot.volume for snapshot in ordered[:-1] if snapshot.volume is not None]
    if latest_volume is None or not baseline_values:
        return VolumeSpikeResult(score=0.0, z_score=0.0)

    baseline = np.array(baseline_values, dtype=float)
    mean = float(np.mean(baseline))
    std = float(np.std(baseline))
    if std == 0.0:
        z_score = 3.0 if float(latest_volume) > mean * 1.5 else 0.0
    else:
        z_score = (float(latest_volume) - mean) / std

    positive_z = max(0.0, z_score)
    score = clamp((float(norm.cdf(positive_z)) - 0.5) * 2.0)
    return VolumeSpikeResult(score=score, z_score=z_score)


def compute_asset_confirmation(
    snapshots_by_symbol: dict[str, Sequence[Any]],
    window_minutes: int,
    full_score_move: float = 0.02,
) -> AssetConfirmationResult:
    moves: dict[str, float] = {}
    scores = []
    for symbol, snapshots in snapshots_by_symbol.items():
        if len(snapshots) < 2:
            continue
        ordered = sorted(snapshots, key=_timestamp)
        latest = ordered[-1]
        latest_time = _timestamp(latest)
        baseline = _baseline_at_or_before(
            ordered,
            latest_time - timedelta(minutes=max(window_minutes, 1)),
        )
        if baseline is None or float(baseline.price) == 0.0:
            continue
        pct_move = (float(latest.price) - float(baseline.price)) / float(baseline.price)
        if not math.isfinite(pct_move):
            continue
        moves[symbol] = pct_move
        scores.append(clamp(abs(pct_move) / full_score_move))

    if not scores:
        return AssetConfirmationResult(score=0.0, moves=moves)
    return AssetConfirmationResult(score=float(np.mean(scores)), moves=moves)


def compute_headline_gap(
    had_headline_before: bool,
    lookback_minutes: int,
    minutes_since_latest_prior_headline: float | None = None,
) -> HeadlineGapResult:
    if not had_headline_before:
        return HeadlineGapResult(
            score=1.0,
            gap_minutes=float(lookback_minutes),
            had_headline_before=False,
        )

    gap_minutes = minutes_since_latest_prior_headline or 0.0
    score = clamp(gap_minutes / float(max(lookback_minutes, 1))) * 0.4
    return HeadlineGapResult(
        score=score,
        gap_minutes=gap_minutes,
        had_headline_before=True,
    )


def combine_signal_score(
    odds_jump_score: float,
    volume_spike_score: float,
    asset_confirmation_score: float,
    headline_gap_score: float,
    topic_importance_score: float,
) -> float:
    return clamp(
        0.30 * odds_jump_score
        + 0.25 * volume_spike_score
        + 0.20 * asset_confirmation_score
        + 0.15 * headline_gap_score
        + 0.10 * clamp(topic_importance_score)
    )

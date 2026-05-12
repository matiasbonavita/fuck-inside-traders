from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fuck_inside_traders.detectors.scoring import (
    combine_signal_score,
    compute_asset_confirmation,
    compute_headline_gap,
    compute_odds_jump,
    compute_volume_spike,
)


@dataclass(frozen=True)
class Snapshot:
    timestamp: datetime
    probability: float = 0.0
    volume: float | None = None
    price: float = 0.0


def test_odds_jump_scores_across_windows() -> None:
    now = datetime.now(UTC)
    result = compute_odds_jump(
        [
            Snapshot(timestamp=now - timedelta(minutes=30), probability=0.35),
            Snapshot(timestamp=now, probability=0.60),
        ],
        [5, 15, 30],
    )

    assert result.score == 1.0
    assert result.odds_jump == 0.25
    assert result.window_minutes == 5


def test_volume_spike_normalizes_positive_z_score() -> None:
    now = datetime.now(UTC)
    result = compute_volume_spike(
        [
            Snapshot(timestamp=now - timedelta(minutes=30), volume=100),
            Snapshot(timestamp=now - timedelta(minutes=20), volume=110),
            Snapshot(timestamp=now - timedelta(minutes=10), volume=105),
            Snapshot(timestamp=now, volume=500),
        ]
    )

    assert result.z_score > 0
    assert 0 < result.score <= 1


def test_asset_confirmation_averages_related_moves() -> None:
    now = datetime.now(UTC)
    result = compute_asset_confirmation(
        {
            "USO": [
                Snapshot(timestamp=now - timedelta(minutes=30), price=100),
                Snapshot(timestamp=now, price=103),
            ],
            "SPY": [
                Snapshot(timestamp=now - timedelta(minutes=30), price=500),
                Snapshot(timestamp=now, price=501),
            ],
        },
        window_minutes=30,
    )

    assert result.moves["USO"] == 0.03
    assert 0 < result.score <= 1


def test_headline_gap_and_combined_score() -> None:
    gap = compute_headline_gap(had_headline_before=False, lookback_minutes=30)
    score = combine_signal_score(1.0, 1.0, 1.0, gap.score, 1.0)

    assert gap.score == 1.0
    assert score == 1.0

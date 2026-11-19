"""Performance smoke benchmarks for a full innings rebuild."""

from __future__ import annotations

import pytest
from tests.scoring.helpers import delivery, innings

from apps.scoring.config import preset_for_format
from apps.scoring.engine import rebuild_innings
from apps.scoring.types import Format

ODI = preset_for_format(Format.ODI)


def _innings(ball_count: int, balls_per_over: int = 6) -> object:
    rows = [
        delivery(
            seq,
            runs=seq % 4,
            bowler=f"b{((seq - 1) // balls_per_over) % 5}",
            over_number=(seq - 1) // balls_per_over + 1,
            ball_in_over=(seq - 1) % balls_per_over + 1,
        )
        for seq in range(1, ball_count + 1)
    ]
    return innings(rows)


@pytest.mark.benchmark
def test_full_odi_300_ball_rebuild(benchmark: object) -> None:
    context = _innings(300)
    result = benchmark(rebuild_innings, context, ODI)  # type: ignore[operator]
    assert result.totals.legal_balls == 300


@pytest.mark.benchmark
def test_test_day_600_ball_rebuild(benchmark: object) -> None:
    context = _innings(600)
    result = benchmark(rebuild_innings, context, ODI)  # type: ignore[operator]
    assert result.totals.legal_balls == 600

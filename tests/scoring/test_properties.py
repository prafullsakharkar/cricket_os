"""Hypothesis properties for the pure engine (TESTING_STRATEGY §2)."""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from tests.scoring.helpers import delivery, innings

from apps.scoring.config import RuleConfig
from apps.scoring.engine import rebuild_innings
from apps.scoring.laws import (
    batting_average,
    legal_balls,
    overs_display,
    safe_div,
    wicket_count,
)
from apps.scoring.types import DismissalType, InningsEventType  # noqa: F401

RULES = RuleConfig(version="property")


@st.composite
def delivery_params(draw: st.DrawFn) -> dict[str, Any]:
    kind = draw(st.sampled_from(["legal", "wide", "noball"]))
    params: dict[str, Any] = {
        "runs": draw(st.integers(min_value=0, max_value=6)),
        "byes": draw(st.integers(min_value=0, max_value=3)),
        "leg_byes": draw(st.integers(min_value=0, max_value=3)),
        "penalty": draw(st.integers(min_value=0, max_value=3)),
    }
    if kind == "wide":
        params["wides"] = draw(st.integers(min_value=1, max_value=3))
    elif kind == "noball":
        params["noballs"] = draw(st.integers(min_value=1, max_value=3))
    return params


@given(st.lists(delivery_params(), max_size=40))
def test_innings_totals_equal_sum_of_parts(params: list[dict[str, Any]]) -> None:
    rows = [delivery(index + 1, **values) for index, values in enumerate(params)]
    totals = rebuild_innings(innings(rows), RULES).totals
    assert totals.runs == sum(row.total_runs for row in rows)
    assert totals.extras.total == sum(row.extras_component for row in rows)
    assert totals.legal_balls == legal_balls(rows)
    assert totals.wickets == 0


@given(
    st.integers(min_value=0, max_value=1000),
    st.integers(min_value=0, max_value=50),
    st.integers(min_value=0, max_value=50),
)
def test_batting_average_never_errors(runs: int, innings_count: int, not_outs: int) -> None:
    not_outs = min(not_outs, innings_count)
    average = batting_average(runs, innings_count, not_outs)
    assert average is None or average >= 0


@given(st.integers(min_value=-1000, max_value=1000), st.integers(min_value=-100, max_value=100))
def test_safe_div_is_none_exactly_on_zero(numerator: int, denominator: int) -> None:
    value = safe_div(numerator, denominator)
    if denominator == 0:
        assert value is None
    else:
        assert value == numerator / denominator


@given(
    st.integers(min_value=0, max_value=1000),
    st.integers(min_value=1, max_value=6),
)
def test_overs_display_uses_the_configured_unit(balls: int, balls_per_over: int) -> None:
    config = RuleConfig(version="v", balls_per_over=balls_per_over)
    display = overs_display(balls, config)
    whole, remainder = display.split(".")
    assert int(whole) == balls // balls_per_over
    assert int(remainder) == balls % balls_per_over
    assert int(remainder) < balls_per_over


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=20),
            st.integers(min_value=0, max_value=6),
        ),
        max_size=40,
    )
)
def test_wicket_count_is_bounded(rows: list[tuple[int, int]]) -> None:
    deliveries = [
        delivery(
            index + 1,
            is_wicket=True,
            dismissal_type=DismissalType.BOWLED,
            dismissed=f"p{sequence}",
            credited=True,
        )
        for index, (sequence, _runs) in enumerate(rows)
    ]
    assert 0 <= wicket_count(deliveries, RULES) <= RULES.max_wickets


@given(st.lists(delivery_params(), max_size=40))
def test_rebuild_is_deterministic(params: list[dict[str, Any]]) -> None:
    rows = [delivery(index + 1, **values) for index, values in enumerate(params)]
    first = rebuild_innings(innings(rows), RULES)
    second = rebuild_innings(innings(list(reversed(rows))), RULES)
    assert first == second

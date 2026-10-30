"""Presets must match the authoritative format_catalogue seed."""

from __future__ import annotations

import pytest

from apps.scoring.config import (
    FORMAT_CATALOGUE,
    PRESETS,
    RuleConfig,
    get_preset,
    preset_for_format,
)
from apps.scoring.errors import InvalidInningsError
from apps.scoring.types import Format


def test_catalogue_matches_schema_seed() -> None:
    expected = {
        Format.TEST: (4, None, 6, True, True, True, False),
        Format.FIRST_CLASS: (4, None, 6, True, True, True, False),
        Format.ODI: (2, 50, 6, False, False, False, True),
        Format.LIST_A: (2, 50, 6, False, False, False, True),
        Format.T20I: (2, 20, 6, False, False, False, True),
        Format.T20: (2, 20, 6, False, False, False, True),
        Format.T10: (2, 10, 6, False, False, False, True),
        Format.THE_HUNDRED: (2, 20, 5, False, False, False, True),
        Format.OTHER: (2, None, 6, False, False, False, True),
    }
    assert set(FORMAT_CATALOGUE) == set(expected)
    for fmt, row in expected.items():
        definition = FORMAT_CATALOGUE[fmt]
        assert (
            definition.max_innings,
            definition.overs_limit,
            definition.balls_per_over,
            definition.allows_declaration,
            definition.allows_follow_on,
            definition.allows_draw,
            definition.allows_super_over,
        ) == row


@pytest.mark.parametrize("name", ["icc-test-2024", "icc-odi-2024", "icc-t20i-2024", "the-hundred"])
def test_preset_matches_catalogue(name: str) -> None:
    preset = PRESETS[name]
    fmt = {
        "icc-test-2024": Format.TEST,
        "icc-odi-2024": Format.ODI,
        "icc-t20i-2024": Format.T20I,
        "the-hundred": Format.THE_HUNDRED,
    }[name]
    row = FORMAT_CATALOGUE[fmt]
    assert preset.version == name
    assert preset.max_innings == row.max_innings
    assert preset.overs_limit == row.overs_limit
    assert preset.balls_per_over == row.balls_per_over
    assert preset.allows_declaration is row.allows_declaration
    assert preset.allows_follow_on is row.allows_follow_on
    assert preset.allows_draw is row.allows_draw
    assert preset.super_over_enabled is row.allows_super_over


def test_preset_for_format_and_generic_fallback() -> None:
    assert preset_for_format(Format.TEST).version == "icc-test-2024"
    assert preset_for_format(Format.LIST_A).version == "icc-odi-2024"
    assert preset_for_format(Format.T20).version == "icc-t20i-2024"
    assert preset_for_format(Format.THE_HUNDRED).balls_per_over == 5
    assert preset_for_format(Format.T10).version == "format-t10"
    assert preset_for_format(Format.OTHER).overs_limit is None


def test_get_preset_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        get_preset("nope")


def test_total_balls_and_max_wickets() -> None:
    assert preset_for_format(Format.ODI).total_balls == 300
    assert preset_for_format(Format.THE_HUNDRED).total_balls == 100
    assert preset_for_format(Format.TEST).total_balls is None
    assert preset_for_format(Format.ODI).max_wickets == 10


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": ""},
        {"version": "x", "balls_per_over": 0},
        {"version": "x", "max_innings": 3},
        {"version": "x", "overs_limit": 0},
        {"version": "x", "team_size": 1},
        {"version": "x", "points_win": -1},
    ],
)
def test_rule_config_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(InvalidInningsError):
        RuleConfig(**kwargs)  # type: ignore[arg-type]

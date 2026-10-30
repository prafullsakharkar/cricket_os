"""Versioned rule configuration and the format catalogue.

The catalogue mirrors the authoritative ``format_catalogue`` seed in
DATABASE_SCHEMA §3.2; the four ICC/Hundred presets must always agree with it
(``tests/scoring/test_config.py`` enforces that).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from apps.scoring.errors import InvalidInningsError
from apps.scoring.types import Format


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """Format-specific assumptions; every output records ``version``."""

    version: str
    balls_per_over: int = 6
    max_innings: int = 2
    overs_limit: int | None = 50
    counts_ball_on_noball: bool = True
    no_ball_byes_off_bat: bool = True
    allows_declaration: bool = False
    allows_follow_on: bool = False
    allows_draw: bool = False
    super_over_enabled: bool = True
    dls_enabled: bool = False
    nrr_full_quota_if_all_out: bool = True
    include_super_over_in_nrr: bool = False
    include_super_over_in_stats: bool = False
    team_size: int = 11
    points_win: int = 2
    points_tie: int = 1
    points_no_result: int = 1
    points_draw: int = 1
    points_loss: int = 0

    def __post_init__(self) -> None:
        if not self.version:
            raise InvalidInningsError("RuleConfig.version is required")
        if self.balls_per_over < 1:
            raise InvalidInningsError("balls_per_over must be >= 1")
        if self.max_innings not in (2, 4):
            raise InvalidInningsError("max_innings must be 2 or 4")
        if self.overs_limit is not None and self.overs_limit < 1:
            raise InvalidInningsError("overs_limit must be >= 1 or None")
        if self.team_size < 2:
            raise InvalidInningsError("team_size must be >= 2")
        for name in (
            "points_win",
            "points_tie",
            "points_no_result",
            "points_draw",
            "points_loss",
        ):
            if getattr(self, name) < 0:
                raise InvalidInningsError(f"{name} must be >= 0")

    @property
    def max_wickets(self) -> int:
        return self.team_size - 1

    @property
    def total_balls(self) -> int | None:
        return None if self.overs_limit is None else self.overs_limit * self.balls_per_over


@dataclass(frozen=True, slots=True)
class FormatDefinition:
    format: Format
    max_innings: int
    overs_limit: int | None
    balls_per_over: int
    allows_declaration: bool
    allows_follow_on: bool
    allows_draw: bool
    allows_super_over: bool


_FORMAT_ROWS: tuple[FormatDefinition, ...] = (
    FormatDefinition(Format.TEST, 4, None, 6, True, True, True, False),
    FormatDefinition(Format.FIRST_CLASS, 4, None, 6, True, True, True, False),
    FormatDefinition(Format.ODI, 2, 50, 6, False, False, False, True),
    FormatDefinition(Format.LIST_A, 2, 50, 6, False, False, False, True),
    FormatDefinition(Format.T20I, 2, 20, 6, False, False, False, True),
    FormatDefinition(Format.T20, 2, 20, 6, False, False, False, True),
    FormatDefinition(Format.T10, 2, 10, 6, False, False, False, True),
    FormatDefinition(Format.THE_HUNDRED, 2, 20, 5, False, False, False, True),
    FormatDefinition(Format.OTHER, 2, None, 6, False, False, False, True),
)

#: Authoritative per-format seed, matching DATABASE_SCHEMA §3.2 row for row.
FORMAT_CATALOGUE: Mapping[Format, FormatDefinition] = MappingProxyType(
    {row.format: row for row in _FORMAT_ROWS}
)


def _config_from_catalogue(version: str, fmt: Format, **overrides: object) -> RuleConfig:
    row = FORMAT_CATALOGUE[fmt]
    values: dict[str, object] = {
        "version": version,
        "balls_per_over": row.balls_per_over,
        "max_innings": row.max_innings,
        "overs_limit": row.overs_limit,
        "allows_declaration": row.allows_declaration,
        "allows_follow_on": row.allows_follow_on,
        "allows_draw": row.allows_draw,
        "super_over_enabled": row.allows_super_over,
    }
    values.update(overrides)
    return RuleConfig(**values)  # type: ignore[arg-type]


ICC_TEST_2024: Final[RuleConfig] = _config_from_catalogue("icc-test-2024", Format.TEST)
ICC_ODI_2024: Final[RuleConfig] = _config_from_catalogue(
    "icc-odi-2024", Format.ODI, dls_enabled=True
)
ICC_T20I_2024: Final[RuleConfig] = _config_from_catalogue(
    "icc-t20i-2024", Format.T20I, dls_enabled=True
)
THE_HUNDRED_RULES: Final[RuleConfig] = _config_from_catalogue("the-hundred", Format.THE_HUNDRED)

#: Named, versioned presets. Values are stable and must match the seed.
PRESETS: Mapping[str, RuleConfig] = MappingProxyType(
    {
        "icc-test-2024": ICC_TEST_2024,
        "icc-odi-2024": ICC_ODI_2024,
        "icc-t20i-2024": ICC_T20I_2024,
        "the-hundred": THE_HUNDRED_RULES,
    }
)

_PRESET_BY_FORMAT: Mapping[Format, RuleConfig] = MappingProxyType(
    {
        Format.TEST: ICC_TEST_2024,
        Format.FIRST_CLASS: ICC_TEST_2024,
        Format.ODI: ICC_ODI_2024,
        Format.LIST_A: ICC_ODI_2024,
        Format.T20I: ICC_T20I_2024,
        Format.T20: ICC_T20I_2024,
        Format.THE_HUNDRED: THE_HUNDRED_RULES,
    }
)


def preset_for_format(fmt: Format) -> RuleConfig:
    """Return the versioned preset for ``fmt`` (generic fallback for T10/OTHER)."""

    preset = _PRESET_BY_FORMAT.get(fmt)
    if preset is not None:
        return preset
    version = f"format-{fmt.value.lower()}"
    return _config_from_catalogue(version, fmt)


def get_preset(name: str) -> RuleConfig:
    try:
        return PRESETS[name]
    except KeyError as exc:
        raise KeyError(f"unknown scoring preset: {name!r}") from exc


#: Spec-default configuration (SCORING_RULES §1). Presets override per format.
DEFAULT_RULES: Final[RuleConfig] = RuleConfig(version="custom")

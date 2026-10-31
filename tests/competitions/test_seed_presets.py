"""The format_catalogue DB seed must agree with the scoring presets.

Both mirror DATABASE_SCHEMA §3.2. The DB seed is authoritative for the
catalogue; apps/scoring/config.py's ``FORMAT_CATALOGUE`` is the pure mirror the
engine reads. This test fails loudly if the two ever drift apart.
"""

from __future__ import annotations

import pytest

from apps.competitions.models import FormatCatalogue
from apps.scoring import config as scoring_config
from apps.scoring.types import Format

pytestmark = pytest.mark.django_db

#: Preset per format, mirroring apps/scoring/config.py._PRESET_BY_FORMAT.
PRESET_BY_FORMAT = {
    Format.TEST: scoring_config.ICC_TEST_2024,
    Format.FIRST_CLASS: scoring_config.ICC_TEST_2024,
    Format.ODI: scoring_config.ICC_ODI_2024,
    Format.LIST_A: scoring_config.ICC_ODI_2024,
    Format.T20I: scoring_config.ICC_T20I_2024,
    Format.T20: scoring_config.ICC_T20I_2024,
    Format.THE_HUNDRED: scoring_config.THE_HUNDRED_RULES,
}


def _preset_for(fmt: Format) -> scoring_config.RuleConfig:
    preset = PRESET_BY_FORMAT.get(fmt)
    if preset is not None:
        return preset
    return scoring_config._config_from_catalogue(f"format-{fmt.value.lower()}", fmt)


@pytest.mark.parametrize("fmt", [f for f in Format if f != Format.OTHER])
def test_seed_row_matches_scoring_preset(fmt: Format) -> None:
    row = FormatCatalogue.objects.get(format=fmt.value)
    preset = _preset_for(fmt)
    assert row.max_innings == preset.max_innings
    assert row.overs_limit == preset.overs_limit
    assert row.balls_per_over == preset.balls_per_over
    assert row.allows_declaration == preset.allows_declaration
    assert row.allows_follow_on == preset.allows_follow_on
    assert row.allows_draw == preset.allows_draw
    assert row.allows_super_over == preset.super_over_enabled


def test_all_nine_ddl_rows_are_seeded() -> None:
    assert FormatCatalogue.objects.count() == 9
    seeded = set(FormatCatalogue.objects.values_list("format", flat=True))
    assert seeded == {fmt.value for fmt in Format}


def test_pure_catalogue_mirrors_seed_verbatim() -> None:
    for fmt, definition in scoring_config.FORMAT_CATALOGUE.items():
        row = FormatCatalogue.objects.get(format=fmt.value)
        assert row.max_innings == definition.max_innings
        assert row.overs_limit == definition.overs_limit
        assert row.balls_per_over == definition.balls_per_over
        assert row.allows_declaration == definition.allows_declaration
        assert row.allows_follow_on == definition.allows_follow_on
        assert row.allows_draw == definition.allows_draw
        assert row.allows_super_over == definition.allows_super_over

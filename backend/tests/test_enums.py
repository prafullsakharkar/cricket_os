"""Enums must match the SQL enum labels in DATABASE_SCHEMA.md §3.1 (drift guard)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from apps.core import enums

pytestmark = pytest.mark.unit

SCHEMA_DOC = Path(__file__).resolve().parents[1] / "docs" / "CRICKET_DATABASE_SCHEMA.md"

ENUM_CLASSES = {
    "team_type": enums.TeamType,
    "format_type": enums.FormatType,
    "match_status": enums.MatchStatus,
    "innings_status": enums.InningsStatus,
    "result_type": enums.ResultType,
    "dismissal_type": enums.DismissalType,
    "retirement_type": enums.RetirementType,
    "innings_event_type": enums.InningsEventType,
    "bat_style": enums.BatStyle,
    "bowl_style": enums.BowlStyle,
    "player_role": enums.PlayerRole,
    "stat_scope": enums.StatScope,
    "ranking_category": enums.RankingCategory,
    "competition_type": enums.CompetitionType,
}


def _doc_enums() -> dict[str, list[str]]:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    found: dict[str, list[str]] = {}
    for match in re.finditer(r"CREATE TYPE cricket\.(\w+)\s+AS ENUM \((.*?)\);", text, re.S):
        found[match.group(1)] = re.findall(r"'([^']*)'", match.group(2))
    return found


@pytest.mark.parametrize("name", sorted(ENUM_CLASSES))
def test_enum_matches_schema_labels_in_order(name: str) -> None:
    doc = _doc_enums()
    assert name in doc, f"{name} is not defined in DATABASE_SCHEMA.md"
    assert [member.value for member in ENUM_CLASSES[name]] == doc[name]


def test_every_schema_enum_is_represented() -> None:
    missing = set(_doc_enums()) - set(ENUM_CLASSES)
    assert not missing, f"enums in the schema doc with no TextChoices: {sorted(missing)}"

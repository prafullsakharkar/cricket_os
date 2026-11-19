"""Competition-domain model constraints (DATABASE_SCHEMA §3.4, §3.2)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.competitions.models import Competition, FormatCatalogue, MatchStage, Season, Series
from apps.core.enums import CompetitionType, FormatType

pytestmark = pytest.mark.django_db


def make_competition(**overrides: object) -> Competition:
    defaults: dict[str, object] = {
        "name": "ICC World Test Championship",
        "competition_type": CompetitionType.LEAGUE,
    }
    defaults.update(overrides)
    return Competition.objects.create(**defaults)  # type: ignore[arg-type]


def make_series(competition: Competition, **overrides: object) -> Series:
    defaults: dict[str, object] = {"name": "Border-Gavaskar Trophy", "competition": competition}
    defaults.update(overrides)
    return Series.objects.create(**defaults)  # type: ignore[arg-type]


class TestCompetition:
    def test_duplicate_name_rejected(self) -> None:
        make_competition()
        with pytest.raises(IntegrityError):
            make_competition()

    def test_duplicate_provider_id_rejected(self) -> None:
        provider_uuid = "33333333-3333-7333-8333-333333333333"
        make_competition(source_provider_id=provider_uuid, provider_competition_id="c1")
        with pytest.raises(IntegrityError):
            make_competition(
                name="Other", source_provider_id=provider_uuid, provider_competition_id="c1"
            )

    def test_competition_type_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            make_competition(competition_type="exhibition")

    def test_format_type_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            make_competition(format_type="ODI_EXTRA")


class TestSeason:
    def test_unique_competition_and_label(self) -> None:
        competition = make_competition()
        Season.objects.create(competition=competition, label="2023-25")
        with pytest.raises(IntegrityError):
            Season.objects.create(competition=competition, label="2023-25")

    def test_same_label_in_different_competition_allowed(self) -> None:
        a = make_competition()
        b = make_competition(name="ICC World Cup")
        Season.objects.create(competition=a, label="2023")
        Season.objects.create(competition=b, label="2023")
        assert Season.objects.count() == 2


class TestSeries:
    def test_duplicate_provider_id_rejected(self) -> None:
        competition = make_competition()
        provider_uuid = "44444444-4444-7444-8444-444444444444"
        make_series(competition, source_provider_id=provider_uuid, provider_series_id="s1")
        with pytest.raises(IntegrityError):
            make_series(
                competition, name="Other", source_provider_id=provider_uuid, provider_series_id="s1"
            )

    def test_series_type_check_constraint(self) -> None:
        competition = make_competition()
        with pytest.raises(IntegrityError):
            make_series(competition, series_type="exhibition")

    def test_series_type_choices_allowed(self) -> None:
        competition = make_competition()
        for stype in ("bilateral", "triangular", "tournament", "league", "icc_event"):
            Series.objects.create(name=stype, competition=competition, series_type=stype)
        assert Series.objects.count() == 5


class TestMatchStage:
    def test_unique_series_and_name(self) -> None:
        competition = make_competition()
        series = make_series(competition)
        MatchStage.objects.create(series=series, name="Semi-final 1", stage_type="semi_final")
        with pytest.raises(IntegrityError):
            MatchStage.objects.create(series=series, name="Semi-final 1", stage_type="semi_final")

    def test_stage_type_check_constraint(self) -> None:
        competition = make_competition()
        series = make_series(competition)
        with pytest.raises(IntegrityError):
            MatchStage.objects.create(series=series, name="Grand final", stage_type="grand_final")


class TestFormatCatalogue:
    def test_max_innings_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            FormatCatalogue.objects.create(format="TEST", max_innings=3)

    def test_format_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            FormatCatalogue.objects.create(format="PARK_CRICKET", max_innings=2)

    def test_format_is_primary_key(self) -> None:
        # The seed already inserted every DDL format; the PK rejects re-inserts.
        assert FormatCatalogue.objects.filter(format="TEST").count() == 1
        with pytest.raises(IntegrityError):
            FormatCatalogue.objects.create(format="TEST", max_innings=2)

    def test_format_value_maps_to_enum(self) -> None:
        for value in FormatType.values:
            FormatCatalogue.objects.update_or_create(format=value, defaults={"max_innings": 2})
        assert FormatCatalogue.objects.count() == len(FormatType.values)

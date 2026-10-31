"""Competition/season/series/format selectors."""

from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.competitions.models import Competition, Season, Series
from apps.competitions.selectors import (
    get_competition,
    get_format,
    get_series,
    list_competitions,
    list_series,
)
from apps.core.enums import CompetitionType, FormatType
from apps.core.errors import NotFound
from apps.venues.models import Country

pytestmark = pytest.mark.django_db


def make_competition(**overrides: object) -> Competition:
    defaults: dict[str, object] = {
        "name": "Atlantis League",
        "competition_type": CompetitionType.LEAGUE,
    }
    defaults.update(overrides)
    return Competition.objects.create(**defaults)  # type: ignore[arg-type]


class TestCompetitionSelectors:
    def test_list_filters_and_loads_governing_body(self) -> None:
        make_competition(name="Atlantis League", is_international=False)
        intl = make_competition(name="Atlantis International", is_international=True)
        assert list(list_competitions(is_international=True)) == [intl]

    def test_get_competition_prefetches_seasons(self) -> None:
        competition = make_competition()
        Season.objects.create(competition=competition, label="2026")
        Season.objects.create(competition=competition, label="2027")
        with CaptureQueriesContext(connection) as queries:
            result = get_competition(competition.pk)
        assert result.seasons.count() == 2
        assert len(queries) == 2  # competition + one seasons query

    def test_get_competition_missing_raises_not_found(self) -> None:
        with pytest.raises(NotFound):
            get_competition("00000000-0000-7000-8000-000000000000")


class TestSeriesSelectors:
    def _series(self, **overrides: object) -> Series:
        defaults: dict[str, object] = {"name": "Atlantis Cup"}
        defaults.update(overrides)
        return Series.objects.create(**defaults)  # type: ignore[arg-type]

    def test_list_filters_by_year_category_and_type(self) -> None:
        competition = make_competition(name="Atlantis Cup")
        host = Country.objects.create(name="Atlantis")
        self._series(
            name="Atlantis Cup 2026",
            competition=competition,
            series_type="tournament",
            start_date=datetime.date(2026, 1, 1),
            host_country=host,
        )
        self._series(
            name="Atlantis Cup 2025",
            competition=competition,
            series_type="tournament",
            start_date=datetime.date(2025, 1, 1),
        )
        self._series(
            name="Atlantis Bilateral 2026",
            series_type="bilateral",
            start_date=datetime.date(2026, 6, 1),
        )

        rows = list_series(year=2026, category=str(competition.pk), series_type="tournament")
        assert list(rows.values_list("name", flat=True)) == ["Atlantis Cup 2026"]

    def test_get_series_loads_relations(self) -> None:
        competition = make_competition(name="Atlantis Cup")
        host = Country.objects.create(name="Atlantis")
        series = self._series(competition=competition, host_country=host)
        with CaptureQueriesContext(connection) as queries:
            result = get_series(series.pk)
        assert result.competition.name == "Atlantis Cup"
        assert result.host_country.name == "Atlantis"
        assert len(queries) == 1

    def test_get_series_missing_raises_not_found(self) -> None:
        with pytest.raises(NotFound):
            get_series("00000000-0000-7000-8000-000000000000")


class TestFormatSelector:
    def test_get_format_returns_seeded_row(self) -> None:
        assert get_format(FormatType.T20).balls_per_over == 6

    def test_get_format_missing_raises_not_found(self) -> None:
        with pytest.raises(NotFound):
            get_format("NOT_A_FORMAT")

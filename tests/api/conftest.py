"""Shared API-test fixtures: deterministic reference-data seed.

Every id is an explicit, fixed UUID (the models' uuid7 default is overridden
by passing ``id=``), so golden payloads, ETags and 301 Locations are fully
deterministic across runs. Countries/GBs are created fresh (never the
migration-seeded rows) to avoid coupling to their generated UUIDs.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from apps.competitions.models import Competition, Season, Series
from apps.players.models import Player
from apps.teams.models import Team
from apps.venues.models import Country, GoverningBody, Venue

CA_COUNTRY = UUID("00000000-0000-4000-8000-000000000001")
CA_GB = UUID("00000000-0000-4000-8000-000000000002")
VENUE = UUID("00000000-0000-4000-8000-000000000003")
TEAM = UUID("00000000-0000-4000-8000-000000000004")
PLAYER = UUID("00000000-0000-4000-8000-000000000005")
COMP = UUID("00000000-0000-4000-8000-000000000006")
SEASON = UUID("00000000-0000-4000-8000-000000000008")
SERIES = UUID("00000000-0000-4000-8000-000000000007")

DOB = datetime.date(1970, 5, 4)


def build_seed_reference() -> SimpleNamespace:
    """Create one of each reference entity with fixed ids (see module doc)."""
    country = Country.objects.create(id=CA_COUNTRY, iso_code="CA", iso3="CAN", name="Canada")
    governing_body = GoverningBody.objects.create(
        id=CA_GB, name="Cricket Canada", type="national", country=country
    )
    venue = Venue.objects.create(
        id=VENUE,
        name="Maple Leaf Cricket Club",
        city="Toronto",
        country=country,
        timezone="America/Toronto",
    )
    team = Team.objects.create(
        id=TEAM,
        name="Canada",
        short_name="CAN",
        abbreviation="CA",
        team_type="international",
        country=country,
        governing_body=governing_body,
        home_venue=venue,
    )
    player = Player.objects.create(
        id=PLAYER,
        full_name="John Davison",
        known_as="JD",
        date_of_birth=DOB,
        gender="M",
        birth_country=country,
        international_team=team,
        batting_style="RIGHT_HAND",
        bowling_style="RIGHT_ARM_OFFBREAK",
        primary_role="ALL_ROUNDER",
        is_wicketkeeper=False,
    )
    competition = Competition.objects.create(
        id=COMP,
        name="Canada Cup",
        short_name="CC",
        competition_type="DOMESTIC",
        format_type="T20",
        is_international=False,
        governing_body=governing_body,
    )
    season = Season.objects.create(
        id=SEASON,
        competition=competition,
        label="2026",
        start_date=datetime.date(2026, 6, 1),
        end_date=datetime.date(2026, 8, 31),
    )
    series = Series.objects.create(
        id=SERIES,
        name="Canada Cup 2026",
        short_name="CC2026",
        competition=competition,
        season=season,
        series_type="tournament",
        start_date=datetime.date(2026, 6, 1),
        end_date=datetime.date(2026, 8, 31),
        host_country=country,
    )
    return SimpleNamespace(
        country=country,
        governing_body=governing_body,
        venue=venue,
        team=team,
        player=player,
        competition=competition,
        season=season,
        series=series,
    )


@pytest.fixture
def seed_reference() -> SimpleNamespace:
    return build_seed_reference()


@pytest.fixture
def second_team(seed_reference: Any) -> Team:
    """A second team sharing the seeded country (for list/ETag tests)."""
    return Team.objects.create(
        name="United States",
        short_name="USA",
        team_type="international",
        country=seed_reference.country,
    )


__all__ = [
    "CA_COUNTRY",
    "CA_GB",
    "COMP",
    "DOB",
    "PLAYER",
    "SEASON",
    "SERIES",
    "TEAM",
    "VENUE",
    "second_team",
    "seed_reference",
]

"""Phase F Group 8: thin endpoints from the coverage audit.

The previously-unassigned spec rows get lean read endpoints: ``/matches``
(all-match list), ``/matches/{id}/overs|squads|facts``, ``/teams/{id}/squad``,
``/series/{id}/matches|squads|venues`` and ``/archives``. Each is cursor- or
bounded-list paginated, cached, and reads current rows only.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.competitions.models import Series
from apps.deliveries.models import Over
from apps.innings.models import Innings
from apps.matches.models import MatchOfficial, MatchPlayer, MatchResult, Toss
from apps.teams.models import TeamMembership
from apps.venues.models import Country, Venue

from ..phase_c.factories import make_match, make_match_teams, make_player, make_team

pytestmark = pytest.mark.django_db


def _match(*, series: Series | None = None, venue: Venue | None = None, **kw: object):
    return make_match(series=series, venue=venue, status="COMPLETED", **kw)


def _venue(name: str = "MCG") -> Venue:
    country, _ = Country.objects.get_or_create(name="Australia", defaults={"iso_code": "AU"})
    return Venue.objects.create(name=name, city="Melbourne", country=country)


# --------------------------------------------------------------------------- #
# /matches (all-match list)
# --------------------------------------------------------------------------- #


def test_matches_list(api_client: object) -> None:
    m1 = _match()
    m2 = _match()
    make_match_teams(m1, make_team("India"), make_team("Australia"))
    make_match_teams(m2, make_team("England"), make_team("Sri Lanka"))

    response = api_client.get("/api/v1/matches")
    assert response.status_code == 200
    body = response.json()
    ids = [row["id"] for row in body["data"]]
    assert str(m1.id) in ids and str(m2.id) in ids
    assert "next_cursor" in body["meta"]


# --------------------------------------------------------------------------- #
# /matches/{id}/overs | squads | facts
# --------------------------------------------------------------------------- #


def test_match_overs_endpoint(api_client: object) -> None:
    match = make_match(status="COMPLETED", format_type="T20I")
    t1, t2 = make_team("India"), make_team("Australia")
    make_match_teams(match, t1, t2)
    inn = Innings.objects.create(
        match=match, innings_number=1, batting_team=t1, bowling_team=t2, status="COMPLETED"
    )
    bowler = make_player("Bowler")
    Over.objects.create(
        innings=inn, over_number=1, bowler=bowler, runs_conceded=6, wickets=1, is_complete=True
    )

    response = api_client.get(f"/api/v1/matches/{match.id}/overs")
    assert response.status_code == 200
    body = response.json()
    over = body["data"][0]
    assert over["over"] == 1
    assert over["bowler"]["name"] == "Bowler"
    assert over["runs"] == 6
    assert over["wickets"] == 1


def test_match_squads_endpoint(api_client: object) -> None:
    match = make_match(status="COMPLETED", format_type="ODI")
    t1 = make_team("India")
    t2 = make_team("Australia")
    make_match_teams(match, t1, t2)
    p1 = make_player("Playing XI")
    p2 = make_player("Bench")
    MatchPlayer.objects.create(
        match=match, player=p1, team=t1, is_playing_xi=True, batting_position=1
    )
    MatchPlayer.objects.create(match=match, player=p2, team=t1, is_playing_xi=False)

    response = api_client.get(f"/api/v1/matches/{match.id}/squads")
    assert response.status_code == 200
    body = response.json()
    teams = body["data"]["teams"]
    india = next(t for t in teams if t["team"]["name"] == "India")
    assert india["playing_xi"] == ["Playing XI"]
    assert india["bench"] == ["Bench"]


def test_match_facts_endpoint(api_client: object) -> None:
    venue = _venue()
    match = make_match(status="COMPLETED", format_type="ODI", venue=venue)
    t1, t2 = make_team("India"), make_team("Australia")
    make_match_teams(match, t1, t2)
    MatchResult.objects.create(
        match=match, result_type="WIN", winner_team=t1, result_text="India won"
    )
    Toss.objects.create(match=match, winner_team=t1, decision="bat")
    MatchOfficial.objects.create(match=match, official_name="Ump A", role="umpire1")

    response = api_client.get(f"/api/v1/matches/{match.id}/facts")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["toss"]["winner"] == "India"
    assert body["toss"]["decision"] == "bat"
    assert body["officials"][0]["name"] == "Ump A"
    assert body["venue"]["name"] == venue.name


# --------------------------------------------------------------------------- #
# /teams/{id}/squad
# --------------------------------------------------------------------------- #


def test_team_squad_endpoint(api_client: object) -> None:
    team = make_team("India")
    p1 = make_player("Active One")
    p2 = make_player("Left Two")
    TeamMembership.objects.create(team=team, player=p1, joined_at=date(2020, 1, 1))
    TeamMembership.objects.create(
        team=team, player=p2, joined_at=date(2020, 1, 1), left_at=date(2024, 1, 1)
    )

    response = api_client.get(f"/api/v1/teams/{team.id}/squad")
    assert response.status_code == 200
    names = [row["name"] for row in response.json()["data"]]
    assert names == ["Active One"]  # left players are excluded


# --------------------------------------------------------------------------- #
# /series/{id}/matches | squads | venues
# --------------------------------------------------------------------------- #


def test_series_matches_endpoint(api_client: object) -> None:
    series = Series.objects.create(name="Test Series", series_type="bilateral")
    m1 = _match(series=series)
    make_match_teams(m1, make_team("India"), make_team("Australia"))
    m2 = _match(series=series)
    make_match_teams(m2, make_team("India"), make_team("Australia"))

    response = api_client.get(f"/api/v1/series/{series.id}/matches")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 2


def test_series_squads_and_venues_endpoints(api_client: object) -> None:
    series = Series.objects.create(name="Test Series", series_type="bilateral")
    venue = _venue()
    match = _match(series=series, venue=venue)
    t1, t2 = make_team("India"), make_team("Australia")
    make_match_teams(match, t1, t2)
    p = make_player("Series Player")
    MatchPlayer.objects.create(match=match, player=p, team=t1, is_playing_xi=True)

    squads = api_client.get(f"/api/v1/series/{series.id}/squads").json()["data"]
    assert squads["India"] == ["Series Player"]

    venues = api_client.get(f"/api/v1/series/{series.id}/venues").json()["data"]
    assert venues[0]["name"] == "MCG"


# --------------------------------------------------------------------------- #
# /archives
# --------------------------------------------------------------------------- #


def test_archives_endpoint(api_client: object) -> None:
    Series.objects.create(name="Old Cup", series_type="tournament", start_date=date(2024, 3, 1))
    Series.objects.create(name="New Cup", series_type="tournament", start_date=date(2026, 3, 1))

    response = api_client.get("/api/v1/archives?year=2026")
    assert response.status_code == 200
    names = [s["name"] for s in response.json()["data"]]
    assert names == ["New Cup"]

"""Phase D: public match-list feeds (CRICKET_API_SPECIFICATION §3.1).

``/matches/live|upcoming|recent``:

* feed semantics (which statuses, ordering, windows), filters
  (``format``/``gender``/``series``/``team``) and cursor pagination;
* state-dependent cache headers + strong ``ETag``/``304`` (spec §3.9);
* ``meta.stale`` when the feeding provider's circuit breaker is OPEN
  (SECURITY §8: serve last-known-good, never fail the read);
* licence flags/attribution from the provider policy (SECURITY §11);
* constant-query rendering (no N+1) and a golden payload shape.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.enums import MatchStatus
from apps.live.services.breaker import CircuitBreaker, reset_breakers
from apps.matches.models import Match

from .test_live_apply import make_context

pytestmark = pytest.mark.django_db


def _client() -> APIClient:
    return APIClient()


def _start(offset_hours: int) -> Any:
    """A start time relative to the test clock (keeps windows deterministic)."""
    return timezone.now() + datetime.timedelta(hours=offset_hours)


def _match(
    *,
    status: str = MatchStatus.IN_PROGRESS,
    start_offset_hours: int = 0,
    end_offset_hours: int | None = None,
    format_type: str = "T20",
    gender: str = "M",
    series: Any = None,
) -> Match:
    from tests.phase_c.factories import make_match, make_match_teams, make_provider, make_team

    provider = make_provider("replay")
    match = make_match(
        format_type=format_type,
        gender=gender,
        status=status,
        start_time_utc=_start(start_offset_hours),
        end_time_utc=_start(end_offset_hours) if end_offset_hours is not None else None,
        source_provider_id=provider.id,
        provider_match_id=f"fx-{start_offset_hours}-{status}-{format_type}",
        series=series,
    )
    team1 = make_team(f"Alpha {start_offset_hours}")
    team2 = make_team(f"Beta {start_offset_hours}")
    make_match_teams(match, team1, team2)
    return match


def _open_breaker_for(provider_code: str) -> CircuitBreaker:
    breaker = CircuitBreaker(f"{provider_code}:poll", provider=provider_code, endpoint="poll")
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    from apps.live.services.breaker import register_breaker

    register_breaker(provider_code, "poll", breaker)
    assert breaker.state_code() == 2  # OPEN
    return breaker


@pytest.fixture(autouse=True)
def _isolate_breakers() -> Any:
    reset_breakers()
    yield
    reset_breakers()


# --------------------------------------------------------------------------- #
# Feed semantics
# --------------------------------------------------------------------------- #


def test_live_list_returns_only_live_matches_newest_first() -> None:
    live = _match(status=MatchStatus.IN_PROGRESS, start_offset_hours=0)
    paused = _match(status=MatchStatus.RAIN_DELAY, start_offset_hours=2)
    done = _match(status=MatchStatus.COMPLETED, start_offset_hours=-3, end_offset_hours=-3)
    scheduled = _match(status=MatchStatus.SCHEDULED, start_offset_hours=5)
    _match(status=MatchStatus.SCHEDULED, start_offset_hours=4)

    body = _client().get("/api/v1/matches/live").json()
    ids = [item["id"] for item in body["data"]]
    assert set(ids) == {str(live.id), str(paused.id)}
    assert ids == [str(paused.id), str(live.id)]  # start_time_utc desc
    assert done.id not in ids and scheduled.id not in ids
    assert body["meta"]["stale"] is False


def test_upcoming_list_window_and_soonest_first() -> None:
    soon = _match(status=MatchStatus.SCHEDULED, start_offset_hours=1)
    later = _match(status=MatchStatus.SCHEDULED, start_offset_hours=48)
    beyond = _match(status=MatchStatus.SCHEDULED, start_offset_hours=24 * 14)
    in_progress = _match(status=MatchStatus.IN_PROGRESS, start_offset_hours=0)

    body = _client().get("/api/v1/matches/upcoming").json()
    ids = [item["id"] for item in body["data"]]
    assert set(ids) == {str(soon.id), str(later.id)}
    assert ids == [str(soon.id), str(later.id)]  # start_time_utc asc
    assert beyond.id not in ids and in_progress.id not in ids


def test_recent_list_returns_terminal_matches_newest_first() -> None:
    finished = _match(status=MatchStatus.COMPLETED, start_offset_hours=-10, end_offset_hours=-9)
    abandoned = _match(status=MatchStatus.ABANDONED, start_offset_hours=-5, end_offset_hours=-4)
    old = _match(
        status=MatchStatus.COMPLETED, start_offset_hours=-24 * 30, end_offset_hours=-24 * 30
    )
    live_now = _match(status=MatchStatus.IN_PROGRESS, start_offset_hours=-1)

    body = _client().get("/api/v1/matches/recent").json()
    ids = [item["id"] for item in body["data"]]
    assert set(ids) == {str(abandoned.id), str(finished.id)}
    assert ids == [str(abandoned.id), str(finished.id)]  # end_time_utc desc
    assert old.id not in ids and live_now.id not in ids


def test_filters_format_gender_series_team() -> None:
    from apps.competitions.models import Competition, Series

    competition = Competition.objects.create(name="League", competition_type="LEAGUE")
    series = Series.objects.create(name="Trophy", competition=competition)

    target = _match(
        status=MatchStatus.IN_PROGRESS,
        start_offset_hours=0,
        format_type="ODI",
        gender="W",
        series=series,
    )
    _match(status=MatchStatus.IN_PROGRESS, start_offset_hours=1, format_type="T20")
    _match(status=MatchStatus.IN_PROGRESS, start_offset_hours=2, gender="W")
    team = target.teams.get(team_number=1).team

    body = (
        _client()
        .get(
            "/api/v1/matches/live",
            data={"format": "ODI", "gender": "W", "series": str(series.id), "team": str(team.id)},
        )
        .json()
    )
    assert [item["id"] for item in body["data"]] == [str(target.id)]


def test_cursor_pagination_is_complete_and_stable() -> None:
    created = [_match(status=MatchStatus.IN_PROGRESS, start_offset_hours=i) for i in range(5)]
    created_ids = {str(m.id) for m in created}

    page1 = _client().get("/api/v1/matches/live", data={"limit": 2}).json()
    assert len(page1["data"]) == 2
    assert page1["meta"]["limit"] == 2
    assert page1["meta"]["has_more"] is True
    assert page1["meta"]["next_cursor"]

    page2 = (
        _client()
        .get("/api/v1/matches/live", data={"limit": 2, "cursor": page1["meta"]["next_cursor"]})
        .json()
    )
    assert len(page2["data"]) == 2
    seen = {item["id"] for item in page1["data"] + page2["data"]}
    assert len(seen) == 4

    page3 = (
        _client()
        .get("/api/v1/matches/live", data={"limit": 2, "cursor": page2["meta"]["next_cursor"]})
        .json()
    )
    assert len(page3["data"]) == 1
    assert page3["meta"]["has_more"] is False
    all_ids = {item["id"] for item in page1["data"] + page2["data"] + page3["data"]}
    assert all_ids == created_ids  # every match exactly once


def test_malformed_cursor_is_400() -> None:
    _match(status=MatchStatus.IN_PROGRESS)
    response = _client().get("/api/v1/matches/live", data={"cursor": "!!!not-base64!!!"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_query"


# --------------------------------------------------------------------------- #
# Caching + ETag (spec §3.9)
# --------------------------------------------------------------------------- #


def test_live_feed_cache_headers_and_etag_304() -> None:
    _match(status=MatchStatus.IN_PROGRESS)
    first = _client().get("/api/v1/matches/live")
    assert first.status_code == 200
    assert first["Cache-Control"] == "public, max-age=5, stale-while-revalidate=5"
    etag = first["ETag"]
    assert etag

    not_modified = _client().get("/api/v1/matches/live", HTTP_IF_NONE_MATCH=etag)
    assert not_modified.status_code == 304
    assert not_modified["Cache-Control"] == "public, max-age=5, stale-while-revalidate=5"
    assert not_modified["ETag"] == etag


def test_list_feed_cache_headers() -> None:
    _match(status=MatchStatus.SCHEDULED, start_offset_hours=6)
    response = _client().get("/api/v1/matches/upcoming")
    assert response["Cache-Control"] == "public, max-age=30, stale-while-revalidate=60"

    _match(status=MatchStatus.COMPLETED, start_offset_hours=-2, end_offset_hours=-1)
    recent = _client().get("/api/v1/matches/recent")
    assert recent["Cache-Control"] == "public, max-age=30, stale-while-revalidate=60"


# --------------------------------------------------------------------------- #
# meta.stale + licence flags
# --------------------------------------------------------------------------- #


def test_meta_stale_when_provider_breaker_is_open() -> None:
    from apps.providers.models import DataProvider

    _match(status=MatchStatus.IN_PROGRESS)  # replay provider
    _open_breaker_for("replay")

    body = _client().get("/api/v1/matches/live").json()
    assert body["meta"]["stale"] is True
    assert all(item["stale"] is True for item in body["data"])

    # A different provider's matches stay fresh.
    provider = DataProvider.objects.create(name="Other", code="other")
    from tests.phase_c.factories import make_match

    make_match(
        status=MatchStatus.IN_PROGRESS,
        start_time_utc=_start(3),
        source_provider_id=provider.id,
        provider_match_id="other-1",
    )
    body2 = _client().get("/api/v1/matches/live").json()
    by_id = {item["id"]: item for item in body2["data"]}
    assert any(item["stale"] is False for item in by_id.values())


def test_attribution_and_license_flags_from_policy() -> None:
    _match(status=MatchStatus.IN_PROGRESS)  # replay: attribution_required=True
    item = _client().get("/api/v1/matches/live").json()["data"][0]
    assert item["ball_data"] is True
    assert item["commentary"] is False
    assert item["attribution"] == "Replay (internal test corpus)"
    assert item["stale"] is False


def test_item_without_provider_has_sane_defaults() -> None:
    from tests.phase_c.factories import make_match, make_match_teams, make_team

    match = make_match(status=MatchStatus.IN_PROGRESS)  # no source_provider_id
    team1 = make_team("No Provider A")
    team2 = make_team("No Provider B")
    make_match_teams(match, team1, team2)
    item = _client().get("/api/v1/matches/live").json()["data"][0]
    assert item["stale"] is False
    assert item["ball_data"] is True
    assert item["attribution"] is None


# --------------------------------------------------------------------------- #
# Query complexity + payload shape
# --------------------------------------------------------------------------- #


def test_rendering_is_constant_query_count_no_n_plus_1() -> None:
    _match(status=MatchStatus.IN_PROGRESS, start_offset_hours=1)
    _match(status=MatchStatus.IN_PROGRESS, start_offset_hours=0)
    small = CaptureQueriesContext(connection)
    with small:
        _client().get("/api/v1/matches/live", data={"limit": 10})
    small_queries = len(small.captured_queries)

    for i in range(3, 9):
        _match(status=MatchStatus.IN_PROGRESS, start_offset_hours=i)
    large = CaptureQueriesContext(connection)
    with large:
        _client().get("/api/v1/matches/live", data={"limit": 10})

    assert len(large.captured_queries) == small_queries
    assert small_queries <= 12  # a handful of __in queries, not N+1


def test_golden_payload_shape() -> None:
    from tests.phase_c.factories import make_provider

    from .test_live_apply import apply_batch, batch

    ctx = make_context()
    match = ctx["match"]
    provider = make_provider("replay")
    Match.objects.filter(id=match.id).update(
        source_provider_id=provider.id, provider_match_id="fx-golden"
    )
    apply_batch(match, [batch(ctx, [1, 2, 3], runs=2)])  # give it a score

    body = _client().get("/api/v1/matches/live", data={"limit": 5}).json()
    assert set(body) == {"data", "meta"}
    assert set(body["meta"]) == {"limit", "has_more", "next_cursor", "stale"}
    item = body["data"][0]
    assert set(item) >= {
        "id",
        "format",
        "gender",
        "status",
        "status_detail",
        "result",
        "toss",
        "teams",
        "venue",
        "start_time",
        "source",
        "overs_limit",
        "revision",
        "updated_at",
        "stale",
        "ball_data",
        "commentary",
        "attribution",
    }
    assert item["status"] == MatchStatus.IN_PROGRESS
    assert {"id", "name", "abbreviation"} <= set(item["teams"][0]["team"])
    assert set(item["teams"][0]["score"]) == {
        "runs",
        "wickets",
        "overs",
        "legal_balls",
        "extras",
        "run_rate",
    }


def test_cursor_boundary_survives_newest_insert() -> None:
    """Keyset pagination: a new match landing ahead of the cursor cannot
    shift or duplicate the page boundary (API_SPEC §6.4)."""
    from tests.phase_c.factories import make_match, make_match_teams, make_team

    for i in range(3):
        match = make_match(
            status=MatchStatus.IN_PROGRESS,
            start_time_utc=_start(i),
        )
        make_match_teams(match, make_team(f"T1-{i}"), make_team(f"T2-{i}"))

    page1 = _client().get("/api/v1/matches/live", data={"limit": 2}).json()
    first_ids = {item["id"] for item in page1["data"]}

    # A new (newest) match lands while the reader holds the cursor.
    make_match(
        status=MatchStatus.IN_PROGRESS,
        start_time_utc=_start(99),
    )
    page2 = (
        _client()
        .get("/api/v1/matches/live", data={"limit": 2, "cursor": page1["meta"]["next_cursor"]})
        .json()
    )
    second_ids = {item["id"] for item in page2["data"]}
    assert first_ids.isdisjoint(second_ids)
    # The remaining original match after the first page is returned exactly
    # once; the newest insert lands ahead of the cursor and stays out of scope.
    assert len(second_ids) == 1

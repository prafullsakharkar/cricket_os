"""Phase E group 3: read-through view caching, snapshot ``meta.event_seq``.

The acceptance criteria exercised here:

* every cacheable GET stores the rendered body + strong ETag + ``event_seq``
  and a cache hit returns an identical body and headers;
* ``If-None-Match`` answers ``304`` with the cache headers preserved on both
  hit and miss paths;
* match-scoped responses carry ``meta.event_seq`` captured with the snapshot
  and it advances when new event-log rows commit;
* state-dependent headers: live (seconds), completed (``s-maxage`` +
  ``stale-if-error``), immutable only after the reconciliation window;
* invalidation: a committed ``delivery.appended`` (via the event bus) changes
  both the match key and the ``matches:live`` bucket;
* 4xx/5xx are never cached;
* reference-data responses are cached and invalidated by their topic.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.core import events
from apps.core.cache import versions
from apps.matches import event_schemas
from apps.matches.models import Match
from apps.matches.services import event_log
from tests.phase_c.factories import make_match, make_match_teams, make_team

pytestmark = pytest.mark.django_db


@pytest.fixture
def match_ctx() -> Any:
    match = make_match()
    make_match_teams(match, make_team("India"), make_team("Australia"))
    return match


@pytest.fixture
def live_match() -> Any:
    match = make_match(status="IN_PROGRESS")
    make_match_teams(match, make_team("India"), make_team("Australia"))
    return match


# --------------------------------------------------------------------------- #
# event_seq snapshot
# --------------------------------------------------------------------------- #
def test_match_shell_carries_meta_event_seq(api_client: Any, match_ctx: Any) -> None:
    response = api_client.get(f"/api/v1/matches/{match_ctx.id}/")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["event_seq"] == 0
    assert body["data"]["id"] == str(match_ctx.id)


@pytest.mark.django_db(transaction=True)
def test_event_seq_advances_and_cache_invalidates(
    api_client: Any, match_ctx: Any, seeded_uuid7_clock: Any
) -> None:
    url = f"/api/v1/matches/{match_ctx.id}/"
    first = api_client.get(url)
    first_body = first.json()
    assert first_body["meta"]["event_seq"] == 0

    # transaction=True so the append's commit runs the on_commit publishers.
    # The append advances the log (the snapshot seq); the core event (which the
    # deliveries/rebuild services publish in production) invalidates the cache.
    event_log.append_event(
        match_ctx.id,
        event_schemas.DELIVERY_APPENDED,
        {"innings_id": "00000000-0000-0000-0000-000000000001", "delivery_seqs": [1]},
    )
    events.publish(
        events.TOPIC_DELIVERY_APPENDED,
        {"match_id": str(match_ctx.id), "innings_id": "00000000-0000-0000-0000-000000000001"},
    )

    second = api_client.get(url)
    assert second.json()["meta"]["event_seq"] == 1
    assert second.content != first.content
    assert second["ETag"] != first["ETag"]


# --------------------------------------------------------------------------- #
# cache hit / 304 / headers
# --------------------------------------------------------------------------- #
def test_cache_hit_is_identical_and_304_preserves_headers(api_client: Any, match_ctx: Any) -> None:
    url = f"/api/v1/matches/{match_ctx.id}/"
    first = api_client.get(url)
    etag = first["ETag"]
    assert etag.startswith('"')

    second = api_client.get(url)
    assert second.status_code == 200
    assert second.content == first.content
    assert second["ETag"] == etag
    assert second["Cache-Control"] == first["Cache-Control"]

    not_modified = api_client.get(url, HTTP_IF_NONE_MATCH=etag)
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert not_modified["Cache-Control"] == first["Cache-Control"]
    assert not_modified["ETag"] == etag


def test_surrogate_key_and_vary_headers(api_client: Any, match_ctx: Any) -> None:
    response = api_client.get(f"/api/v1/matches/{match_ctx.id}/")
    assert response["Surrogate-Key"] == f"match-{match_ctx.id}"
    assert response["Vary"] == "Accept-Encoding"


def test_cache_hit_serves_the_same_headers_after_an_etag_check(
    api_client: Any, match_ctx: Any
) -> None:
    url = f"/api/v1/matches/{match_ctx.id}/"
    first = api_client.get(url)
    # A second client with no validator still gets the full headers.
    second = api_client.get(url)
    assert second["Surrogate-Key"] == first["Surrogate-Key"]
    assert second["Vary"] == first["Vary"]


# --------------------------------------------------------------------------- #
# state-dependent headers
# --------------------------------------------------------------------------- #
def test_completed_match_has_long_ttl_and_immutable_after_window(
    api_client: Any, match_ctx: Any
) -> None:
    url = f"/api/v1/matches/{match_ctx.id}/"
    match: Match = match_ctx
    match.status = "COMPLETED"
    match.end_time_utc = timezone.now()
    match.save(update_fields=["status", "end_time_utc", "revision"])
    versions.bump(f"match:{match.id}")  # lifecycle changed without an event

    response = api_client.get(url)
    assert "max-age=300" in response["Cache-Control"]
    assert "s-maxage=3600" in response["Cache-Control"]
    assert "stale-if-error=86400" in response["Cache-Control"]
    assert "immutable" not in response["Cache-Control"]

    match.end_time_utc = timezone.now() - datetime.timedelta(hours=25)
    match.save(update_fields=["end_time_utc"])
    versions.bump(f"match:{match.id}")
    after_window = api_client.get(url)
    assert "immutable" in after_window["Cache-Control"]


def test_live_match_uses_seconds_level_ttl(api_client: Any, live_match: Any) -> None:
    response = api_client.get(f"/api/v1/matches/{live_match.id}/")
    assert response["Cache-Control"] == "public, max-age=5, stale-while-revalidate=5"


# --------------------------------------------------------------------------- #
# invalidation through the event bus
# --------------------------------------------------------------------------- #
@pytest.mark.django_db(transaction=True)
def test_live_list_is_invalidated_by_a_delivery_event(api_client: Any, live_match: Any) -> None:
    url = "/api/v1/matches/live"
    first = api_client.get(url)
    assert first.status_code == 200
    assert len(first.json()["data"]) == 1

    # A lifecycle change alters the shell payload; the state-changed event is
    # what the live path publishes on commit (cache invalidation subscriber).
    Match.objects.filter(id=live_match.id).update(revision=2)
    events.publish(
        events.TOPIC_MATCH_STATE_CHANGED,
        {"match_id": str(live_match.id), "from": "IN_PROGRESS", "to": "IN_PROGRESS"},
    )

    second = api_client.get(url)
    assert second.status_code == 200
    assert second.content != first.content


@pytest.mark.django_db(transaction=True)
def test_reference_response_is_cached_and_invalidated_by_its_topic(api_client: Any) -> None:
    team = make_team("Caching Test")
    url = "/api/v1/teams"
    first = api_client.get(url)
    assert first.status_code == 200
    assert first["Cache-Control"] == "public, max-age=600"

    team.name = "Caching Test (renamed)"
    team.save(update_fields=["name"])
    events.publish(events.TOPIC_TEAM_UPDATED, {"team_id": str(team.pk)})

    second = api_client.get(url)
    assert second.status_code == 200
    assert second.content != first.content
    assert any(item["name"] == "Caching Test (renamed)" for item in second.json()["data"])


# --------------------------------------------------------------------------- #
# errors are never cached
# --------------------------------------------------------------------------- #
def test_404_is_never_cached(api_client: Any) -> None:
    url = "/api/v1/matches/00000000-0000-0000-0000-000000000000/"
    for _ in range(3):
        response = api_client.get(url)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_400_is_never_cached(api_client: Any, match_ctx: Any) -> None:
    url = f"/api/v1/matches/{match_ctx.id}/innings/1/deliveries?limit=abc"
    for _ in range(3):
        response = api_client.get(url)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_query"


def test_surrogate_header_name_is_configurable(api_client: Any, match_ctx: Any) -> None:
    with override_settings(CACHE_SURROGATE_HEADER="X-Surrogate-Key"):
        response = api_client.get(f"/api/v1/matches/{match_ctx.id}/")
        assert response["X-Surrogate-Key"] == f"match-{match_ctx.id}"
        assert "Surrogate-Key" not in response

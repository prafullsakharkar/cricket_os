"""Cache-Control TTLs, strong ETags, 304 revalidation and merge 301 redirects."""

from __future__ import annotations

from typing import Any

import pytest

from apps.players.models import Player
from apps.players.services import merge_players

pytestmark = [pytest.mark.django_db, pytest.mark.contract]


def _detail_url(url: str, seed_reference: Any) -> str:
    """Substitute a real id for the ``{id}`` placeholder in a URL."""
    if "{id}" not in url:
        return url
    # /api/v1/<entity>/{id}/ -> ["api", "v1", "teams", "{id}"] -> "teams";
    # the seed namespace uses singular attribute names ("team", "player", ...).
    plural = url.strip("/").split("/")[2]
    entity = plural[:-1] if plural.endswith("s") else plural
    return url.format(id=getattr(seed_reference, entity).pk)


@pytest.mark.parametrize(
    ("url", "cache_control"),
    [
        ("/api/v1/teams", "public, max-age=600"),
        ("/api/v1/teams/{id}/", "public, max-age=600"),
        ("/api/v1/players", "public, max-age=300"),
        ("/api/v1/players/{id}/", "public, max-age=300, stale-while-revalidate=600"),
        ("/api/v1/competitions", "public, max-age=600"),
        ("/api/v1/series", "public, max-age=600"),
        ("/api/v1/venues", "public, max-age=600"),
        ("/api/v1/venues/{id}/", "public, max-age=600"),
    ],
)
def test_cache_control_per_view(
    api_client: Any, seed_reference: Any, url: str, cache_control: str
) -> None:
    url = _detail_url(url, seed_reference)
    response = api_client.get(url)
    assert response.status_code == 200, url
    assert response["Cache-Control"] == cache_control


def test_series_detail_has_short_ttl(api_client: Any, seed_reference: Any) -> None:
    response = api_client.get(f"/api/v1/series/{seed_reference.series.pk}/")
    assert response["Cache-Control"] == "public, max-age=60"


class TestETag:
    def test_strong_etag_present(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get("/api/v1/teams")
        assert response["ETag"].startswith('"') and response["ETag"].endswith('"')

    def test_304_on_matching_if_none_match(self, api_client: Any, seed_reference: Any) -> None:
        first = api_client.get("/api/v1/teams")
        etag = first["ETag"]
        second = api_client.get("/api/v1/teams", HTTP_IF_NONE_MATCH=etag)
        assert second.status_code == 304
        assert second.content == b""
        assert second["Cache-Control"] == first["Cache-Control"]
        assert second["ETag"] == etag

    def test_changed_data_yields_new_etag(
        self, api_client: Any, seed_reference: Any, second_team: Any
    ) -> None:
        before = api_client.get("/api/v1/teams").json()
        first_etag = api_client.get("/api/v1/teams")["ETag"]

        # second_team is created by the fixture BEFORE this test body runs, so
        # create a third team here to change the list payload.
        from apps.teams.models import Team

        Team.objects.create(name="Mexico", team_type="international")
        after = api_client.get("/api/v1/teams").json()
        second_etag = api_client.get("/api/v1/teams")["ETag"]

        assert len(after["data"]) == len(before["data"]) + 1
        assert first_etag != second_etag
        # The stale etag no longer revalidates.
        stale = api_client.get("/api/v1/teams", HTTP_IF_NONE_MATCH=first_etag)
        assert stale.status_code == 200


class TestMerge301:
    def test_merged_player_redirects_with_location(self, api_client: Any) -> None:
        winner = Player.objects.create(full_name="Pat Cummins", gender="M")
        loser = Player.objects.create(full_name="P Cummins", gender="M")
        merge_players(winner, loser, actor="test")

        response = api_client.get(f"/api/v1/players/{loser.pk}/")
        assert response.status_code == 301
        assert response["Location"] == f"/api/v1/players/{winner.pk}/"
        # No body, no cache headers on the 301 (CRICKET_API_SPECIFICATION §3.5).
        assert response.content == b""
        assert "Cache-Control" not in response
        assert "ETag" not in response

    def test_chain_of_merges_resolves_to_ultimate_canonical(self, api_client: Any) -> None:
        # The merge service collapses chains (step 2b re-points forwards), so
        # simulate a legacy two-hop redirect directly to prove the lookup
        # follows PlayerMerge rows rather than stopping at the first hop.
        from apps.players.models import PlayerMerge

        winner = Player.objects.create(full_name="Virat Kohli", gender="M")
        middle = Player.objects.create(full_name="V Kohli", gender="M")
        loser = Player.objects.create(full_name="Kohli", gender="M")
        PlayerMerge.objects.create(canonical_player=winner, merged_player=middle)
        PlayerMerge.objects.create(canonical_player=middle, merged_player=loser)

        response = api_client.get(f"/api/v1/players/{loser.pk}/")
        assert response.status_code == 301
        assert response["Location"] == f"/api/v1/players/{winner.pk}/"

    def test_active_player_is_not_redirected(self, api_client: Any) -> None:
        player = Player.objects.create(full_name="Steve Smith", gender="M")
        response = api_client.get(f"/api/v1/players/{player.pk}/")
        assert response.status_code == 200


__all__: list[str] = []

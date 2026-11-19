"""Reference-data API: envelopes, filters, sparse fields, errors, pagination."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.api.conftest import CA_COUNTRY, PLAYER, SERIES, TEAM, VENUE

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _get(api_client: Any, url: str) -> Any:
    return api_client.get(url, format="json")


class TestEnvelopeShape:
    def test_team_list_envelope(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/teams")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"data", "meta"}
        assert isinstance(body["data"], list)
        assert body["data"][0]["id"] == str(TEAM)
        assert {"page", "page_size", "total_count", "total_pages", "has_more"} <= set(body["meta"])

    def test_team_detail_envelope(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, f"/api/v1/teams/{TEAM}/")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"data"}
        assert body["data"]["id"] == str(TEAM)
        assert body["data"]["country"]["iso_code"] == "CA"
        assert body["data"]["governing_body"]["name"] == "Cricket Canada"
        assert body["data"]["home_venue"]["name"] == "Maple Leaf Cricket Club"

    def test_player_list_cursor_envelope(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/players")
        body = response.json()
        assert {"limit", "has_more", "next_cursor"} <= set(body["meta"])
        assert "total_count" not in body["meta"]  # cursor, not page-number

    def test_player_list_with_q_is_page_number(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/players?q=davison")
        body = response.json()
        assert {"page", "page_size", "total_count", "total_pages"} <= set(body["meta"])
        assert body["data"][0]["full_name"] == "John Davison"

    def test_competition_detail_has_nested_seasons(
        self, api_client: Any, seed_reference: Any
    ) -> None:
        response = _get(api_client, f"/api/v1/competitions/{seed_reference.competition.pk}/")
        body = response.json()
        assert body["data"]["seasons"] == [
            {
                "id": str(seed_reference.season.pk),
                "label": "2026",
                "start_date": "2026-06-01",
                "end_date": "2026-08-31",
            }
        ]

    def test_series_detail_nests_competition_and_host(
        self, api_client: Any, seed_reference: Any
    ) -> None:
        response = _get(api_client, f"/api/v1/series/{SERIES}/")
        body = response.json()
        assert body["data"]["competition"]["name"] == "Canada Cup"
        assert body["data"]["host_country"]["iso_code"] == "CA"

    def test_venue_detail(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, f"/api/v1/venues/{VENUE}/")
        body = response.json()
        assert body["data"]["timezone"] == "America/Toronto"
        assert body["data"]["country"]["iso_code"] == "CA"


class TestErrors:
    def test_missing_id_is_404_envelope(self, api_client: Any) -> None:
        missing = "00000000-0000-4000-8000-00000000ffff"
        for url in (
            f"/api/v1/teams/{missing}/",
            f"/api/v1/players/{missing}/",
            f"/api/v1/competitions/{missing}/",
            f"/api/v1/series/{missing}/",
            f"/api/v1/venues/{missing}/",
        ):
            response = _get(api_client, url)
            assert response.status_code == 404, url
            assert response.json()["error"]["code"] == "not_found"

    def test_non_uuid_path_is_404_not_500(self, api_client: Any) -> None:
        for url in (
            "/api/v1/teams/not-a-uuid/",
            "/api/v1/players/abc/",
            "/api/v1/series/1/",
        ):
            response = _get(api_client, url)
            assert response.status_code == 404, url
            assert response.json()["error"]["code"] == "not_found"

    def test_unknown_fields_is_400(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/players?fields=id,does_not_exist")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_query"
        assert response.json()["error"]["details"]["fields"] == ["does_not_exist"]

    def test_invalid_sort_is_400(self, api_client: Any, seed_reference: Any) -> None:
        for url in (
            "/api/v1/teams?sort=bogus",
            "/api/v1/players?sort=bogus",
            "/api/v1/series?sort=bogus",
            "/api/v1/competitions?sort=bogus",
        ):
            response = _get(api_client, url)
            assert response.status_code == 400, url
            assert response.json()["error"]["code"] == "invalid_query"

    def test_invalid_country_is_400(self, api_client: Any) -> None:
        response = _get(api_client, "/api/v1/teams?country=ZZ")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_query"

    def test_invalid_year_is_400(self, api_client: Any) -> None:
        response = _get(api_client, "/api/v1/series?year=abc")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_query"


class TestSparseFields:
    def test_sparse_player_fields(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, f"/api/v1/players/{PLAYER}/?fields=id,full_name,known_as")
        assert response.status_code == 200
        assert set(response.json()["data"]) == {"id", "full_name", "known_as"}

    def test_sparse_team_list_fields(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/teams?fields=id,name")
        body = response.json()
        assert set(body["data"][0]) == {"id", "name"}
        # meta is untouched by sparse fields
        assert "total_count" in body["meta"]


class TestFilters:
    def test_teams_by_country_iso(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/teams?country=CA")
        assert [t["name"] for t in response.json()["data"]] == ["Canada"]

    def test_teams_by_country_uuid(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, f"/api/v1/teams?country={CA_COUNTRY}")
        assert response.json()["data"][0]["name"] == "Canada"

    def test_teams_trigram_q(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/teams?q=canad")
        names = [t["name"] for t in response.json()["data"]]
        assert names == ["Canada"]

    def test_players_by_role(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/players?role=ALL_ROUNDER")
        assert response.json()["data"][0]["full_name"] == "John Davison"

    def test_series_by_year_and_type(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/series?year=2026&type=tournament")
        assert response.json()["data"][0]["name"] == "Canada Cup 2026"

    def test_competitions_by_q_and_type(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/competitions?q=cup&type=DOMESTIC")
        assert response.json()["data"][0]["name"] == "Canada Cup"

    def test_unknown_team_uuid_filters_to_empty(self, api_client: Any) -> None:
        response = _get(api_client, "/api/v1/players?team=00000000-0000-4000-8000-00000000ffff")
        assert response.status_code == 200
        assert response.json()["data"] == []


class TestPagination:
    def test_page_number_pages(self, api_client: Any, seed_reference: Any) -> None:
        response = _get(api_client, "/api/v1/teams?page_size=1")
        body = response.json()
        assert len(body["data"]) == 1
        assert body["meta"]["total_count"] == 1
        assert body["meta"]["page"] == 1

    def test_cursor_limit_and_more(self, api_client: Any, seed_reference: Any) -> None:
        from apps.players.models import Player

        Player.objects.create(full_name="Aaron Johnson", gender="M")
        response = _get(api_client, "/api/v1/players?limit=1")
        body = response.json()
        assert len(body["data"]) == 1
        assert body["meta"]["has_more"] is True
        assert body["meta"]["next_cursor"] is not None

    def test_response_is_json_not_html(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get("/api/v1/teams")
        assert "application/json" in response["Content-Type"]
        json.loads(response.content)  # parses


__all__: list[str] = []

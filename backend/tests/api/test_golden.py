"""Golden-file payloads: deterministic rendered JSON for team/player/series.

The seed uses fixed explicit UUIDs (tests/api/conftest.py), so these payloads
must not change unless the API contract changes deliberately. Regenerate with
``scripts/regenerate_api_golden.py`` after an intentional change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.api.conftest import PLAYER, SERIES, TEAM

pytestmark = [pytest.mark.django_db, pytest.mark.contract]

GOLDEN = Path(__file__).resolve().parent / "golden"


def _golden(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


class TestGolden:
    def test_team_detail(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get(f"/api/v1/teams/{TEAM}/")
        assert response.status_code == 200
        assert response.json() == _golden("team.json")

    def test_player_detail(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get(f"/api/v1/players/{PLAYER}/")
        assert response.status_code == 200
        assert response.json() == _golden("player.json")

    def test_series_detail(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get(f"/api/v1/series/{SERIES}/")
        assert response.status_code == 200
        assert response.json() == _golden("series.json")

    def test_players_page(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get("/api/v1/players?limit=2")
        assert response.status_code == 200
        assert response.json() == _golden("players_page.json")


__all__: list[str] = []

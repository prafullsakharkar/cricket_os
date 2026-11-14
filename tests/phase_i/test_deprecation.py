"""Phase I Task 7 (§11.2): `Sunset` deprecation headers + 410 + additive-only.

The mechanism (``apps/core/deprecation.py``) ships with an empty registry in
production; these tests register a deprecated view_name against real reference
endpoints to prove the headers, the ``410 Gone`` path and that the response
**body is untouched** (additive-only compatibility — existing golden payloads
keep validating, which ``tests/api/test_golden.py`` and
``tests/api/test_schema_contract.py`` re-verify on every ``make check``).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest

from apps.core import deprecation

pytestmark = [pytest.mark.django_db, pytest.mark.contract]

#: Fixed id matching ``tests/api/conftest.py``'s TEAM so the additive-only
#: assertion compares against the golden payload shape.
TEAM = UUID("00000000-0000-4000-8000-000000000004")


@pytest.fixture
def seed_team() -> None:
    """One international team (the reference-entity seed, minus the rest)."""
    from apps.teams.models import Team

    Team.objects.create(
        id=TEAM,
        name="Canada",
        short_name="CAN",
        abbreviation="CA",
        team_type="international",
    )


#: A real view_name (``apps/teams/api/urls.py``) the tests deprecate.
TEAM_DETAIL = "teams:team-detail"
TEAM_LIST = "teams:team-list"
SUNSET = date(2027, 2, 14)
REPLACEMENT = "/api/v2/teams/{id}"


@pytest.fixture(autouse=True)
def _isolate_deprecation_registry() -> object:
    deprecation.reset()
    yield
    deprecation.reset()


def test_sunset_and_link_headers_appended_to_response(api_client: object, seed_team: None) -> None:
    """A deprecated endpoint carries ``Sunset`` + ``Link: successor-version``."""
    deprecation.register(
        deprecation.DeprecationMeta(endpoint=TEAM_DETAIL, sunset_at=SUNSET, replacement=REPLACEMENT)
    )
    response = api_client.get(f"/api/v1/teams/{TEAM}/")  # type: ignore[attr-defined]
    assert response.status_code == 200
    assert response.headers["Sunset"] == "2027-02-14"
    assert f'<{REPLACEMENT}>; rel="successor-version"' in response.headers["Link"]


def test_deprecated_without_sunset_date_omits_header(api_client: object, seed_team: None) -> None:
    """Deprecated but not yet scheduled -> no ``Sunset`` header is emitted."""
    deprecation.register(deprecation.DeprecationMeta(endpoint=TEAM_DETAIL))
    response = api_client.get(f"/api/v1/teams/{TEAM}/")  # type: ignore[attr-defined]
    assert response.status_code == 200
    assert "Sunset" not in response.headers


def test_fully_sunset_endpoint_answers_410_gone(api_client: object, seed_team: None) -> None:
    """A ``gone`` endpoint answers 410 before the view runs."""
    deprecation.register(
        deprecation.DeprecationMeta(endpoint=TEAM_DETAIL, sunset_at=SUNSET, gone=True)
    )
    response = api_client.get(f"/api/v1/teams/{TEAM}/")  # type: ignore[attr-defined]
    assert response.status_code == 410
    body = response.json()
    assert body["error"]["code"] == "gone"


def test_undeprecated_endpoint_untouched(api_client: object, seed_team: None) -> None:
    """Endpoints not in the registry never see deprecation headers."""
    deprecation.register(
        deprecation.DeprecationMeta(endpoint=TEAM_DETAIL, sunset_at=SUNSET, replacement=REPLACEMENT)
    )
    response = api_client.get("/api/v1/teams")  # type: ignore[attr-defined]
    assert response.status_code == 200
    assert "Sunset" not in response.headers
    assert "Link" not in response.headers


def test_additive_only_response_body_unchanged(api_client: object, seed_team: None) -> None:
    """Headers are additive: the payload is byte-for-byte identical."""
    baseline = api_client.get(f"/api/v1/teams/{TEAM}/")  # type: ignore[attr-defined]
    deprecation.register(
        deprecation.DeprecationMeta(endpoint=TEAM_DETAIL, sunset_at=SUNSET, replacement=REPLACEMENT)
    )
    deprecated = api_client.get(f"/api/v1/teams/{TEAM}/")  # type: ignore[attr-defined]
    assert deprecated.status_code == 200
    assert deprecated.json() == baseline.json()
    assert set(deprecated.headers) >= set(baseline.headers)


def test_registry_is_empty_by_default() -> None:
    """No live endpoint is deprecated in Phase I (production registry empty)."""
    assert deprecation.deprecations() == {}


def test_link_append_preserves_existing_link_header(api_client: object) -> None:
    """An existing ``Link`` header is preserved (comma-joined, RFC 8288)."""
    deprecation.register(
        deprecation.DeprecationMeta(endpoint=TEAM_DETAIL, sunset_at=SUNSET, replacement=REPLACEMENT)
    )

    class _Client:  # tiny stand-in: just exercise _append_link directly
        pass

    from django.http import HttpResponse

    response = HttpResponse(b"{}", content_type="application/json")
    response["Link"] = '</teams/1>; rel="canonical"'
    deprecation._append_link(response, REPLACEMENT)
    assert response.headers["Link"] == (
        f'</teams/1>; rel="canonical", <{REPLACEMENT}>; rel="successor-version"'
    )

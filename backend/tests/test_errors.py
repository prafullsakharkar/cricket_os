"""Error envelope + DRF exception handler mapping (API_SPECIFICATION §1 catalog)."""

from __future__ import annotations

import pytest
from rest_framework import exceptions as drf

from apps.core import errors

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("exc_class", "code", "status_code"),
    [
        (errors.InvalidQuery, "invalid_query", 400),
        (errors.Unauthenticated, "unauthenticated", 401),
        (errors.Forbidden, "forbidden", 403),
        (errors.LicenseRequired, "license_required", 403),
        (errors.NotFound, "not_found", 404),
        (errors.Conflict, "conflict", 409),
        (errors.Unprocessable, "unprocessable", 422),
        (errors.RateLimited, "rate_limited", 429),
        (errors.UpstreamUnavailable, "upstream_unavailable", 503),
    ],
)
def test_exception_hierarchy_matches_catalog(
    exc_class: type[errors.CricketError], code: str, status_code: int
) -> None:
    exc = exc_class()
    assert isinstance(exc, errors.CricketError)
    assert exc.code == code
    assert exc.status_code == status_code


def test_error_payload_shape() -> None:
    payload = errors.error_payload("not_found", "missing", {"id": "x"})
    assert payload == {"error": {"code": "not_found", "message": "missing", "details": {"id": "x"}}}


def test_error_payload_details_defaults_to_none() -> None:
    payload = errors.error_payload("conflict", "dup")
    assert payload["error"]["details"] is None


def test_handler_maps_cricket_error() -> None:
    response = errors.drf_exception_handler(errors.NotFound("no such match"), context={})
    assert response is not None
    assert response.status_code == 404
    assert response.data["error"]["code"] == "not_found"
    assert response.data["error"]["message"] == "no such match"


def test_handler_maps_drf_validation_error_to_unprocessable() -> None:
    response = errors.drf_exception_handler(drf.ValidationError({"format": ["bad"]}), context={})
    assert response is not None
    assert response.status_code == 422
    assert response.data["error"]["code"] == "unprocessable"


def test_handler_maps_drf_not_found() -> None:
    response = errors.drf_exception_handler(drf.NotFound(), context={})
    assert response is not None
    assert response.status_code == 404
    assert response.data["error"]["code"] == "not_found"


def test_handler_maps_drf_permission_denied() -> None:
    response = errors.drf_exception_handler(drf.PermissionDenied(), context={})
    assert response is not None
    assert response.status_code == 403
    assert response.data["error"]["code"] == "forbidden"


def test_handler_maps_drf_unauthenticated() -> None:
    response = errors.drf_exception_handler(drf.NotAuthenticated(), context={})
    assert response is not None
    assert response.status_code == 401
    assert response.data["error"]["code"] == "unauthenticated"


def test_handler_maps_throttled_with_retry_after_header() -> None:
    response = errors.drf_exception_handler(drf.Throttled(wait=42), context={})
    assert response is not None
    assert response.status_code == 429
    assert response.data["error"]["code"] == "rate_limited"
    assert response["Retry-After"] == "42"


def test_handler_leaks_nothing_for_unexpected_exceptions() -> None:
    response = errors.drf_exception_handler(RuntimeError("secret internals"), context={})
    assert response is not None
    assert response.status_code == 500
    assert response.data["error"]["code"] == "internal_error"
    assert response.data["error"]["message"] == "Internal server error"
    assert "secret internals" not in str(response.data)


def test_handler_maps_django_http404_to_not_found() -> None:
    from django.http import Http404

    response = errors.drf_exception_handler(Http404(), context={})
    assert response is not None
    assert response.status_code == 404
    assert response.data["error"]["code"] == "not_found"

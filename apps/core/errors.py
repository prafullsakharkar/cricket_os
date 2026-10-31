"""The standard error envelope and the DRF exception handler.

Every API error is rendered as::

    {"error": {"code": "...", "message": "...", "details": ...}}

``drf_exception_handler`` maps both Cricket OS exceptions and DRF/Django
framework exceptions onto the catalog in API_SPECIFICATION §1. Unhandled
exceptions become a generic 500 whose body leaks nothing; details go to logs.
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler

logger = logging.getLogger("apps.core.errors")

#: Human-readable defaults per catalog code.
CODE_MESSAGES: dict[str, str] = {
    "invalid_query": "Invalid query parameters",
    "unauthenticated": "Authentication required",
    "forbidden": "Forbidden",
    "license_required": "License required",
    "not_found": "Not found",
    "conflict": "Conflict",
    "unprocessable": "Unprocessable entity",
    "rate_limited": "Rate limit exceeded",
    "upstream_unavailable": "Upstream dependency unavailable",
    "internal_error": "Internal server error",
}

#: DRF/Django response status → catalog code. ``400`` maps to ``422`` because
#: a body/field validation failure is "unprocessable", not a malformed query.
_STATUS_TO_CODE: dict[int, str] = {
    400: "unprocessable",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "invalid_query",
    406: "invalid_query",
    409: "conflict",
    415: "unprocessable",
    422: "unprocessable",
    429: "rate_limited",
    503: "upstream_unavailable",
}

#: Canonical HTTP status per catalog code (used to re-map, e.g. 400 → 422).
_CODE_STATUS: dict[str, int] = {
    "invalid_query": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "license_required": 403,
    "not_found": 404,
    "conflict": 409,
    "unprocessable": 422,
    "rate_limited": 429,
    "upstream_unavailable": 503,
    "internal_error": 500,
}


class CricketError(Exception):
    """Base class for errors that carry a catalog code and HTTP status."""

    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str | None = None, details: Any = None) -> None:
        self.message = message if message is not None else CODE_MESSAGES.get(self.code, self.code)
        self.details = details
        super().__init__(self.message)


class InvalidQuery(CricketError):
    code = "invalid_query"
    status_code = 400


class Unauthenticated(CricketError):
    code = "unauthenticated"
    status_code = 401


class Forbidden(CricketError):
    code = "forbidden"
    status_code = 403


class LicenseRequired(CricketError):
    code = "license_required"
    status_code = 403


class NotFound(CricketError):
    code = "not_found"
    status_code = 404


class Conflict(CricketError):
    code = "conflict"
    status_code = 409


class Unprocessable(CricketError):
    code = "unprocessable"
    status_code = 422


class RateLimited(CricketError):
    code = "rate_limited"
    status_code = 429

    def __init__(
        self, message: str | None = None, details: Any = None, retry_after: int | None = None
    ) -> None:
        super().__init__(message, details)
        self.retry_after = retry_after


class UpstreamUnavailable(CricketError):
    code = "upstream_unavailable"
    status_code = 503


def error_payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    """Build the canonical ``{"error": {...}}`` envelope."""
    return {"error": {"code": code, "message": message, "details": details}}


def _envelope(
    code: str,
    message: str,
    status_code: int,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> Response:
    response = Response(error_payload(code, message, details), status=status_code)
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def drf_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Render any exception as the standard envelope.

    Returns ``None`` only when DRF should handle it (never, in practice — this
    handler always produces a response so nothing escapes unenveloped).
    """
    if isinstance(exc, CricketError):
        headers = {}
        if isinstance(exc, RateLimited) and exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        return _envelope(exc.code, exc.message, exc.status_code, exc.details, headers)

    response = drf_default_handler(exc, context)
    if response is None:
        # Unhandled: log the internals, return nothing to the client.
        logger.exception("unhandled exception", exc_info=exc)
        return _envelope("internal_error", CODE_MESSAGES["internal_error"], 500)

    code = _STATUS_TO_CODE.get(response.status_code, "invalid_query")
    details = _details_from(response.data)
    message = CODE_MESSAGES.get(code, "Error")
    status_code = _CODE_STATUS.get(code, response.status_code)
    headers = {key: str(value) for key, value in response.headers.items()}
    return _envelope(code, message, status_code, details, headers)


def _details_from(data: Any) -> Any:
    """Extract structured details from a DRF error body (dict/list only)."""
    if isinstance(data, (dict, list)):
        return data
    return None


def handler404(request: Any, exception: Any = None) -> Any:
    """Envelope a URL-level 404 (unmatched path / non-UUID id)."""
    from django.http import JsonResponse

    return JsonResponse(
        error_payload("not_found", CODE_MESSAGES["not_found"]),
        status=404,
    )


def handler500(request: Any) -> Any:
    """Envelope an unhandled URL-level error without leaking internals."""
    from django.http import JsonResponse

    return JsonResponse(
        error_payload("internal_error", CODE_MESSAGES["internal_error"]),
        status=500,
    )


__all__ = [
    "CODE_MESSAGES",
    "Conflict",
    "CricketError",
    "Forbidden",
    "Http404",
    "InvalidQuery",
    "LicenseRequired",
    "NotFound",
    "RateLimited",
    "Unauthenticated",
    "Unprocessable",
    "UpstreamUnavailable",
    "drf_exception_handler",
    "drf_exceptions",
    "error_payload",
    "handler404",
    "handler500",
]

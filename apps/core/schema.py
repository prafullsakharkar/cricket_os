"""drf-spectacular schema customisation: the standard error responses.

Every public endpoint can answer with the standard error envelope
(400 invalid_query, 404 not_found, 405 method_not_allowed, 429 rate_limited,
500 internal_error — see apps/core/errors.py). drf-spectacular only declares
the success responses by default, so a schema-driven fuzzer (schemathesis)
flags every non-2xx as an undocumented status code (F-02, ST103). This schema
class appends the standard error responses to every response-direction
operation without clobbering a view's own declared responses.
"""

from __future__ import annotations

from typing import Any, Literal

from drf_spectacular.openapi import AutoSchema

#: The ``{"error": {"code", "message", "details"}}`` envelope (apps/core/errors.py).
ERROR_ENVELOPE: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {"type": "string", "example": "invalid_query"},
                "message": {"type": "string", "example": "Invalid query parameters"},
                "details": {},
            },
        },
    },
}

#: The standard error statuses every public endpoint can return, keyed by
#: the catalog code each one carries (apps/core/errors.py).
STANDARD_ERROR_RESPONSES: dict[str, dict[str, Any]] = {
    str(status): {
        "description": description,
        "content": {"application/json": {"schema": ERROR_ENVELOPE}},
    }
    for status, description in (
        (400, "Invalid query parameters (invalid_query)"),
        (404, "Not found (not_found)"),
        (405, "Method not allowed (method_not_allowed)"),
        (429, "Rate limit exceeded (rate_limited)"),
        (500, "Internal server error (internal_error)"),
    )
}


class CricketOSSchema(AutoSchema):
    """AutoSchema that declares the standard error responses on every endpoint."""

    def _get_response_bodies(
        self, direction: Literal["request", "response"] = "response"
    ) -> dict[str, Any]:
        responses = super()._get_response_bodies(direction)
        if direction == "response":
            for status, schema in STANDARD_ERROR_RESPONSES.items():
                responses.setdefault(status, schema)
        return responses

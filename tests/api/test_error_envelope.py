"""Standard error envelope + 405 method-not-allowed contract (Phase I F-03/F-02).

F-03: DRF answers undeclared HTTP methods with 405 MethodNotAllowed, but the
error handler re-mapped that to a 400 invalid_query envelope. These tests pin
the 405 so a schema-driven fuzzer (schemathesis ST204) and real clients get
the REST-correct status.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.django_db


def _error(response: Any) -> dict[str, Any]:
    return json.loads(response.content)["error"]


class TestUnsupportedMethodsReturn405:
    @pytest.mark.parametrize("method", ["TRACE", "PUT", "PATCH", "DELETE"])
    def test_undeclared_method_is_405_with_envelope(self, api_client: Any, method: str) -> None:
        response = api_client.generic(method, "/api/v1/matches/live")
        assert response.status_code == 405, f"{method} should be 405"
        err = _error(response)
        assert err["code"] == "method_not_allowed"
        assert err["message"] == "Method not allowed"
        assert "detail" in err["details"]


def _load_schema(api_client: Any) -> dict[str, Any]:
    response = api_client.get("/api/v1/schema", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    return json.loads(response.content)


STANDARD_ERROR_STATUSES = ("400", "404", "405", "429", "500")


class TestStandardErrorResponsesDeclared:
    def test_every_operation_declares_the_standard_error_statuses(self, api_client: Any) -> None:
        doc = _load_schema(api_client)
        operations = [
            (method, path, op)
            for path, methods in doc["paths"].items()
            for method, op in methods.items()
            if isinstance(op, dict) and "responses" in op
        ]
        assert operations, "no operations found in the schema"
        for method, path, op in operations:
            responses = op["responses"]
            for status in STANDARD_ERROR_STATUSES:
                assert status in responses, f"{method.upper()} {path} does not declare {status}"

    def test_error_response_uses_the_standard_envelope(self, api_client: Any) -> None:
        doc = _load_schema(api_client)
        responses = doc["paths"]["/api/v1/matches/live"]["get"]["responses"]
        schema = responses["400"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "error" in schema["required"]

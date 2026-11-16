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

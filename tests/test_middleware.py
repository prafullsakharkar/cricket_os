"""Request-id middleware: generate/echo header, appear in logs."""

from __future__ import annotations

import logging
import uuid

import pytest
from django.test import Client

pytestmark = pytest.mark.integration

HEALTH = "/api/v1/health"


def test_generates_request_id_when_absent() -> None:
    response = Client().get(HEALTH)
    assert response.headers["X-Request-ID"]
    uuid.UUID(response.headers["X-Request-ID"])  # must be a valid UUID


def test_echoes_supplied_request_id() -> None:
    response = Client().get(HEALTH, headers={"X-Request-ID": "trace-123"})
    assert response.headers["X-Request-ID"] == "trace-123"


def test_request_id_appears_in_log_records(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        Client().get(HEALTH, headers={"X-Request-ID": "trace-xyz"})
    assert any(getattr(record, "request_id", None) == "trace-xyz" for record in caplog.records)


def test_middleware_logs_structured_request_fields(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        Client().get(HEALTH)
    request_logs = [r for r in caplog.records if getattr(r, "path", None) == HEALTH]
    assert request_logs, "middleware should log the request path"
    record = request_logs[0]
    assert record.method == "GET"
    assert isinstance(record.status, int)
    assert record.duration_ms >= 0

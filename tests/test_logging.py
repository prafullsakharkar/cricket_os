"""Structured JSON logging: request-id binding and secret redaction."""

from __future__ import annotations

import json
import logging

import pytest

from apps.core.logging import (
    JsonFormatter,
    RedactionFilter,
    RequestIDLogFilter,
    get_request_id,
    redact,
    reset_request_id,
    set_request_id,
)

pytestmark = pytest.mark.unit


def _record(message: str = "hello") -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, message, None, None)


def test_redact_masks_sensitive_keys_recursively() -> None:
    data = {
        "authorization": "Bearer abc",
        "nested": {"password": "p", "keep": 1},
        "items": [{"token": "t"}],
        "api_key": "k",
        "credential": "c",
    }
    out = redact(data)
    assert out["authorization"] == "***"
    assert out["nested"]["password"] == "***"
    assert out["nested"]["keep"] == 1
    assert out["items"][0]["token"] == "***"
    assert out["api_key"] == "***"
    assert out["credential"] == "***"


def test_redact_is_case_insensitive() -> None:
    assert redact({"Authorization": "x", "API_KEY": "y"}) == {
        "Authorization": "***",
        "API_KEY": "***",
    }


def test_json_formatter_emits_parseable_json_with_request_id() -> None:
    set_request_id("req-1")
    try:
        record = _record("request handled")
        payload = json.loads(JsonFormatter().format(record))
    finally:
        reset_request_id()
    assert payload["request_id"] == "req-1"
    assert payload["message"] == "request handled"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"


def test_json_formatter_redacts_sensitive_extra_fields() -> None:
    record = _record()
    record.authorization = "Bearer secret"  # type: ignore[attr-defined]
    record.api_key = "abc"  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["authorization"] == "***"
    assert payload["api_key"] == "***"


def test_redaction_filter_masks_sensitive_record_attributes() -> None:
    record = _record()
    record.password = "p"  # type: ignore[attr-defined]
    record.token = "t"  # type: ignore[attr-defined]
    assert RedactionFilter().filter(record) is True
    assert record.password == "***"  # type: ignore[attr-defined]
    assert record.token == "***"  # type: ignore[attr-defined]


def test_request_id_log_filter_sets_attribute() -> None:
    set_request_id("req-2")
    try:
        record = _record()
        assert RequestIDLogFilter().filter(record) is True
        assert record.request_id == "req-2"  # type: ignore[attr-defined]
    finally:
        reset_request_id()


def test_request_id_context_defaults_to_none() -> None:
    reset_request_id()
    assert get_request_id() is None

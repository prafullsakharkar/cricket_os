"""Structured JSON logging with request-id binding and secret redaction.

The formatter emits one JSON object per line. A contextvar carries the current
request id so every log line — including Celery worker logs — can be correlated
with the originating HTTP request.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

#: Field names whose values are masked wherever they are logged.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "secret",
        "client_secret",
        "password",
        "api_key",
        "apikey",
        "credential",
        "credentials",
    }
)

#: Attributes present on every ``logging.LogRecord``; never echoed as "extra".
_RESERVED_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)

#: Extra keys the middleware always emits (promoted to first-class fields).
_REQUEST_FIELDS = ("method", "path", "status", "duration_ms")


def get_request_id() -> str | None:
    """Return the request id bound to the current context, if any."""
    return _request_id_var.get()


def set_request_id(value: str | None) -> Token[str | None]:
    """Bind a request id to the current context; returns a reset token."""
    return _request_id_var.set(value)


def reset_request_id(token: Token[str | None] | None = None) -> None:
    """Reset the request id, either to a token's prior value or to ``None``."""
    if token is None:
        _request_id_var.set(None)
    else:
        _request_id_var.reset(token)


def redact(value: Any) -> Any:
    """Recursively mask sensitive keys in dicts/lists; other values pass through."""
    if isinstance(value, dict):
        return {
            key: ("***" if isinstance(key, str) and key.lower() in SENSITIVE_KEYS else redact(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or get_request_id(),
        }
        for field in _REQUEST_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key in payload or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str, separators=(",", ":"))


class RequestIDLogFilter(logging.Filter):
    """Ensure every record carries a ``request_id`` attribute."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id() or "-"
        return True


class RedactionFilter(logging.Filter):
    """Mask any record attribute whose name is sensitive."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__):
            if key.lower() in SENSITIVE_KEYS:
                record.__dict__[key] = "***"
        return True


def _timestamp(created: float) -> str:
    value = datetime.fromtimestamp(created, tz=UTC)
    if value.microsecond == 0:
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

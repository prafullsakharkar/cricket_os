"""Payload redaction for the ``IngestionError`` DLQ (DATA_INGESTION §4).

Every DLQ payload is passed through :func:`redact` before it is stored so no
credential material, token or secret ever reaches the table — even inside
provider error bodies that echo the request. Redaction is key-based and
recursive: any mapping key whose normalized name contains a sensitive word
(``password``, ``secret``, ``token``, ``credential``, ``api_key``,
``authorization``, ``auth``) is replaced with a constant placeholder, and
nested dicts/lists are walked the same way. Non-sensitive values pass through
unchanged, so the DLQ still carries the diagnostic shape of the failure.
"""

from __future__ import annotations

from typing import Any

#: Normalised key fragments that mark a value as sensitive.
_SENSITIVE_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "authorization",
    "auth",
)

#: Placeholder written in place of a redacted value.
REDACTED = "[REDACTED]"


def is_sensitive_key(key: str) -> bool:
    """True when ``key`` (any case/spacing) looks like a credential field."""
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_PARTS)


def redact(value: Any) -> Any:
    """Recursively replace sensitive values; return everything else unchanged.

    Scalars and containers without sensitive keys are returned as-is (a plain
    string payload stays plain). ``None`` stays ``None``.
    """
    if isinstance(value, dict):
        return {k: (REDACTED if is_sensitive_key(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


__all__ = ["REDACTED", "is_sensitive_key", "redact"]

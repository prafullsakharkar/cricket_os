"""Durable per-match event types and versioned payload schemas (API_SPEC §3.8).

The event log is the source of truth for realtime delivery: each row is a tiny
delta (a delivery, a state change), never a whole scorecard. Every payload is
versioned with ``v`` so Phase E consumers can evolve independently.

``EVENT_SCHEMAS`` is a JSON-Schema draft-2020-12 map used by the test suite to
validate every event type; runtime code only checks the type is known (cheap).
"""

from __future__ import annotations

from typing import Any

#: Event type names (API_SPECIFICATION §3.8 / DOMAIN_MODEL §3.12).
DELIVERY_APPENDED = "delivery.appended"
DELIVERY_CORRECTED = "delivery.corrected"
INNINGS_UPDATED = "innings.updated"
MATCH_STATE_CHANGED = "match.state_changed"
MATCH_UPDATED = "match.updated"
MATCH_COMPLETED = "match.completed"
CORRECTION_APPLIED = "correction.applied"
SCORECARD_UPDATED = "scorecard.updated"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        DELIVERY_APPENDED,
        DELIVERY_CORRECTED,
        INNINGS_UPDATED,
        MATCH_STATE_CHANGED,
        MATCH_UPDATED,
        MATCH_COMPLETED,
        CORRECTION_APPLIED,
        SCORECARD_UPDATED,
    }
)

SCHEMA_VERSION = 1

_STRING_OR_NULL = {"type": ["string", "null"]}
_INT_OR_NULL = {"type": ["integer", "null"]}


def _schema(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "v": {"type": "integer", "const": SCHEMA_VERSION},
            "match_id": {"type": "string"},
            **properties,
        },
        "required": ["v", "match_id", *required],
    }


EVENT_SCHEMAS: dict[str, dict[str, Any]] = {
    DELIVERY_APPENDED: _schema(
        {
            "innings_id": {"type": "string"},
            "delivery_seqs": {"type": "array", "items": {"type": "integer"}},
            "revision": {"type": "integer"},
        },
        ("innings_id", "delivery_seqs"),
    ),
    DELIVERY_CORRECTED: _schema(
        {
            "innings_id": {"type": "string"},
            "delivery_seqs": {"type": "array", "items": {"type": "integer"}},
            "revision": {"type": "integer"},
        },
        ("innings_id", "delivery_seqs"),
    ),
    INNINGS_UPDATED: _schema(
        {
            "innings_id": {"type": "string"},
            "runs": {"type": "integer"},
            "wickets": {"type": "integer"},
            "overs_display": _STRING_OR_NULL,
            "digest": {"type": "string"},
        },
        ("innings_id",),
    ),
    MATCH_STATE_CHANGED: _schema(
        {
            "from": {"type": "string"},
            "to": {"type": "string"},
            "state_title": _STRING_OR_NULL,
            "status_detail": _STRING_OR_NULL,
            "reopened": {"type": "boolean"},
            "source": _STRING_OR_NULL,
        },
        ("from", "to"),
    ),
    MATCH_UPDATED: _schema(
        {
            "fields": {"type": "object"},
            "reason": _STRING_OR_NULL,
            "source": _STRING_OR_NULL,
        },
        ("fields",),
    ),
    MATCH_COMPLETED: _schema(
        {
            "result_type": {"type": "string"},
            "winner_team_id": _STRING_OR_NULL,
            "margin_type": _STRING_OR_NULL,
            "margin_value": _INT_OR_NULL,
            "result_text": _STRING_OR_NULL,
            "is_dls": {"type": "boolean"},
            "is_super_over": {"type": "boolean"},
            "digest": {"type": "string"},
        },
        ("result_type",),
    ),
    CORRECTION_APPLIED: _schema(
        {
            "entity_type": {"type": "string"},
            "entity_id": {"type": "string"},
            "field_path": _STRING_OR_NULL,
            "reason": _STRING_OR_NULL,
            "source_revision": _INT_OR_NULL,
            "target_revision": {"type": "integer"},
            "result_changed": {"type": "boolean"},
        },
        ("entity_type", "entity_id", "target_revision"),
    ),
    SCORECARD_UPDATED: _schema(
        {
            "innings_id": _STRING_OR_NULL,
            "digest": {"type": "string"},
            "revision": {"type": "integer"},
        },
        ("digest",),
    ),
}


def envelope(
    event_type: str, match_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a versioned payload with the required ``v``/``match_id`` fields."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type!r}")
    return {"v": SCHEMA_VERSION, "match_id": str(match_id), **(payload or {})}


__all__ = [
    "CORRECTION_APPLIED",
    "DELIVERY_APPENDED",
    "DELIVERY_CORRECTED",
    "EVENT_SCHEMAS",
    "EVENT_TYPES",
    "INNINGS_UPDATED",
    "MATCH_COMPLETED",
    "MATCH_STATE_CHANGED",
    "MATCH_UPDATED",
    "SCHEMA_VERSION",
    "SCORECARD_UPDATED",
    "envelope",
]

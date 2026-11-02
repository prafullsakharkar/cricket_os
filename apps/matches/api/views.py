"""Match read API: shell, scorecard, innings, deliveries (API_SPEC §3.2–§3.4).

Four endpoints under ``/api/v1/matches/{id}/``:

* ``GET /matches/{id}/`` — match shell + state (spec §4.1);
* ``GET /matches/{id}/scorecard`` — innings-indexed cards + FOW +
  partnerships + merged retirements, ``?innings=N`` optional (spec §4.2);
* ``GET /matches/{id}/innings`` — innings list & progression;
* ``GET /matches/{id}/innings/{n}/deliveries`` — partition-pruned
  ball-by-ball with a cursor on ``delivery_seq``.

All four use :class:`CacheControlMixin` with **state-dependent TTLs**
(spec §3.9): live matches are seconds-level (``max-age=5, swr=5``),
terminal matches long-lived (``max-age=300, swr=600``). The strong ETag is
the digest of the rendered payload, so a correction (revision bump) changes
it automatically and ``If-None-Match`` answers ``304``.

The deliveries endpoint cannot use the core ``CursorPagination`` (its
tiebreaker is the model PK, which is a composite ``(id, match_date)`` for the
partitioned ``delivery`` table), so it implements the same opaque cursor
codec over ``delivery_seq`` alone — unique per innings, so no tiebreaker is
needed.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import InvalidQuery
from apps.core.http import CacheControlMixin
from apps.matches.selectors.reads import (
    TERMINAL_STATUSES,
    _delivery_payload,
    deliveries_queryset,
    get_innings,
    get_match,
    list_innings,
    match_scorecard,
    match_shell,
)

_LIVE_MAX_AGE = 5
_LIVE_SWR = 5
_COMPLETED_MAX_AGE = 300
_COMPLETED_SWR = 600

_DELIVERY_DEFAULT_LIMIT = 50
_DELIVERY_MAX_LIMIT = 100


def _cache_ttl(match: Any) -> tuple[int, int | None]:
    """Per-match cache TTLs: live is seconds-level, terminal is long-lived."""
    if match.status in TERMINAL_STATUSES:
        return _COMPLETED_MAX_AGE, _COMPLETED_SWR
    return _LIVE_MAX_AGE, _LIVE_SWR


def _as_int_param(request: Any, name: str, *, minimum: int | None = None) -> int | None:
    """Parse an integer query parameter, rejecting junk with ``invalid_query``."""
    raw = request.query_params.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidQuery(f"{name} must be an integer", details={name: raw}) from exc
    if minimum is not None and value < minimum:
        raise InvalidQuery(f"{name} must be >= {minimum}", details={name: raw})
    return value


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


class MatchDetailView(CacheControlMixin, APIView):
    """``GET /matches/{id}/`` — match shell + state."""

    def get(self, request: Any, pk: str) -> Response:
        match = get_match(pk)
        self.cache_max_age, self.cache_swr = _cache_ttl(match)
        return Response({"data": match_shell(match)})


class ScorecardView(CacheControlMixin, APIView):
    """``GET /matches/{id}/scorecard`` — innings-indexed cards."""

    def get(self, request: Any, pk: str) -> Response:
        match = get_match(pk)
        innings_number = _as_int_param(request, "innings", minimum=1)
        if innings_number is not None and innings_number > 4:
            raise InvalidQuery("innings must be 1..4", details={"innings": innings_number})
        self.cache_max_age, self.cache_swr = _cache_ttl(match)
        return Response({"data": match_scorecard(match, innings_number=innings_number)})


class InningsView(CacheControlMixin, APIView):
    """``GET /matches/{id}/innings`` — innings list & progression."""

    def get(self, request: Any, pk: str) -> Response:
        match = get_match(pk)
        self.cache_max_age, self.cache_swr = _cache_ttl(match)
        return Response({"data": list_innings(match)})


def _encode_delivery_cursor(delivery_seq: int) -> str:
    raw = json.dumps({"seq": delivery_seq}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_delivery_cursor(cursor: str) -> int:
    """Decode a delivery cursor; malformed cursors are a 400 ``invalid_query``."""
    try:
        raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        seq = int(payload["seq"])
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidQuery("Malformed cursor", details={"cursor": cursor}) from exc
    if seq < 1:
        raise InvalidQuery("Malformed cursor", details={"cursor": cursor})
    return seq


class DeliveriesView(CacheControlMixin, APIView):
    """``GET /matches/{id}/innings/{n}/deliveries`` — ball-by-ball feed.

    Filters: ``from_seq``, ``to_seq``, ``over``. Pagination is a cursor on
    ``delivery_seq`` (unique per innings), ``limit`` default 50 max 100.
    Reads ``is_current=True`` rows only, pruned to the match's year partition
    via ``match_date``.
    """

    def get(self, request: Any, pk: str, innings_number: int) -> Response:
        match = get_match(pk)
        innings = get_innings(match, innings_number)

        from_seq = _as_int_param(request, "from_seq", minimum=1)
        to_seq = _as_int_param(request, "to_seq", minimum=1)
        over = _as_int_param(request, "over", minimum=1)
        limit = _as_int_param(request, "limit", minimum=1)
        page_size = min(limit or _DELIVERY_DEFAULT_LIMIT, _DELIVERY_MAX_LIMIT)

        qs = (
            deliveries_queryset(
                match,
                innings,
                from_seq=from_seq,
                to_seq=to_seq,
                over=over,
            )
            .select_related(
                "bowler",
                "batter",
                "non_striker",
                "dismissed_player",
                "retired_player",
                "fielder1",
                "fielder2",
                "fielder3",
            )
            .order_by("delivery_seq")
        )

        cursor = request.query_params.get("cursor")
        if cursor:
            qs = qs.filter(delivery_seq__gt=_decode_delivery_cursor(cursor))

        rows = list(qs[: page_size + 1])
        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]
            next_cursor = _encode_delivery_cursor(rows[-1].delivery_seq)
        else:
            next_cursor = None

        self.cache_max_age, self.cache_swr = _cache_ttl(match)
        return Response(
            {
                "data": [_delivery_payload(row) for row in rows],
                "meta": {
                    "limit": page_size,
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                    "ball_data": True,
                    "commentary": False,
                },
            }
        )


__all__ = [
    "DeliveriesView",
    "InningsView",
    "MatchDetailView",
    "ScorecardView",
]

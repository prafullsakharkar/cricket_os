"""Match read API: shell, scorecard, innings, deliveries (API_SPEC §3.2–§3.4).

Four endpoints under ``/api/v1/matches/{id}/``:

* ``GET /matches/{id}/`` — match shell + state (spec §4.1);
* ``GET /matches/{id}/scorecard`` — innings-indexed cards + FOW +
  partnerships + merged retirements, ``?innings=N`` optional (spec §4.2);
* ``GET /matches/{id}/innings`` — innings list & progression;
* ``GET /matches/{id}/innings/{n}/deliveries`` — partition-pruned
  ball-by-ball with a cursor on ``delivery_seq``.

Every response is served through :class:`~apps.core.cache_views.CachedReadMixin`
with **state-dependent TTLs** (spec §3.9 / PERFORMANCE §5): live matches are
seconds-level (``max-age=5, swr=5``), terminal matches long-lived with a CDN
TTL and ``stale-if-error``, and ``immutable`` appears only after the
post-completion reconciliation window. The strong ETag is the digest of the
rendered payload, so a correction (revision bump) changes it automatically and
``If-None-Match`` answers ``304``.

Every match-scoped payload carries **``meta.event_seq``**: the highest
event-log ``seq`` reflected in the snapshot (API_SPEC §3.8). It is captured
atomically with the data via :func:`consistent_snapshot`, so a client can load
REST and subscribe from ``meta.event_seq`` without gaps or duplicates.

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
import time
from typing import Any

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cache.policy import for_match
from apps.core.cache_views import CachedReadMixin
from apps.core.errors import InvalidQuery, NotFound
from apps.matches.selectors.reads import (
    _delivery_payload,
    consistent_snapshot,
    current_event_seq,
    deliveries_queryset,
    get_innings,
    get_match,
    list_innings,
    match_facts,
    match_overs,
    match_scorecard,
    match_shell,
    match_squads,
)

_DELIVERY_DEFAULT_LIMIT = 50
_DELIVERY_MAX_LIMIT = 100


def _ended_at(match: Any) -> float | None:
    """Epoch seconds of ``end_time_utc`` (the immutable-window input)."""
    return match.end_time_utc.timestamp() if match.end_time_utc else None


class _MatchCachedView(CachedReadMixin, APIView):
    """Shared caching identity for all ``/matches/{id}`` sub-resources."""

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"match:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"match-{kwargs['pk']}",)

    def cache_plan(self) -> Any:
        match = getattr(self, "_match", None)
        if match is None:
            try:
                match = get_match(self.kwargs["pk"])
            except NotFound:
                # A missing match is a 404 (never cached); any plan is fine.
                return for_match("", ended_at=None, now=time.time())
        return for_match(match.status, ended_at=_ended_at(match), now=time.time())


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


class MatchDetailView(_MatchCachedView):
    """``GET /matches/{id}/`` — match shell + state."""

    cache_namespace = "match:shell"

    def get(self, request: Any, pk: str) -> Response:
        with consistent_snapshot():
            match = get_match(pk)
            data = match_shell(match)
            seq = current_event_seq(match.id)
        self._match = match
        self._event_seq = seq
        return Response({"data": data, "meta": {"event_seq": seq}})


class ScorecardView(_MatchCachedView):
    """``GET /matches/{id}/scorecard`` — innings-indexed cards."""

    cache_namespace = "match:scorecard"

    def get(self, request: Any, pk: str) -> Response:
        innings_number = _as_int_param(request, "innings", minimum=1)
        if innings_number is not None and innings_number > 4:
            raise InvalidQuery("innings must be 1..4", details={"innings": innings_number})
        with consistent_snapshot():
            match = get_match(pk)
            data = match_scorecard(match, innings_number=innings_number)
            seq = current_event_seq(match.id)
        data["meta"]["event_seq"] = seq
        self._match = match
        self._event_seq = seq
        return Response({"data": data})


class InningsView(_MatchCachedView):
    """``GET /matches/{id}/innings`` — innings list & progression."""

    cache_namespace = "match:innings"

    def get(self, request: Any, pk: str) -> Response:
        with consistent_snapshot():
            match = get_match(pk)
            data = list_innings(match)
            seq = current_event_seq(match.id)
        data["meta"]["event_seq"] = seq
        self._match = match
        self._event_seq = seq
        return Response({"data": data})


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


class DeliveriesView(_MatchCachedView):
    """``GET /matches/{id}/innings/{n}/deliveries`` — ball-by-ball feed.

    Filters: ``from_seq``, ``to_seq``, ``over``. Pagination is a cursor on
    ``delivery_seq`` (unique per innings), ``limit`` default 50 max 100.
    Reads ``is_current=True`` rows only, pruned to the match's year partition
    via ``match_date``.
    """

    cache_namespace = "match:deliveries"
    # Ball-level endpoint: stricter token bucket than the default scope (§7).
    throttle_scope = "deliveries"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"], str(kwargs["innings_number"]))

    def get(self, request: Any, pk: str, innings_number: int) -> Response:
        from_seq = _as_int_param(request, "from_seq", minimum=1)
        to_seq = _as_int_param(request, "to_seq", minimum=1)
        over = _as_int_param(request, "over", minimum=1)
        limit = _as_int_param(request, "limit", minimum=1)
        page_size = min(limit or _DELIVERY_DEFAULT_LIMIT, _DELIVERY_MAX_LIMIT)

        with consistent_snapshot():
            match = get_match(pk)
            innings = get_innings(match, innings_number)

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
            seq = current_event_seq(match.id)

        self._match = match
        self._event_seq = seq
        return Response(
            {
                "data": [_delivery_payload(row) for row in rows],
                "meta": {
                    "limit": page_size,
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                    "ball_data": True,
                    "commentary": False,
                    "event_seq": seq,
                },
            }
        )


# --------------------------------------------------------------------------- #
# Coverage-audit thin endpoints (Group 8): overs / squads / facts
# --------------------------------------------------------------------------- #


class MatchOversView(_MatchCachedView):
    """``GET /matches/{id}/overs`` — over-by-over summary (API_SPEC §3.2)."""

    cache_namespace = "match:overs"

    def get(self, request: Any, pk: str) -> Response:
        match = get_match(pk)
        self._match = match
        rows = match_overs(pk)
        return Response({"data": rows, "meta": {"count": len(rows)}})


class MatchSquadsView(_MatchCachedView):
    """``GET /matches/{id}/squads`` — playing XI + bench per team (§3.2)."""

    cache_namespace = "match:squads"

    def get(self, request: Any, pk: str) -> Response:
        match = get_match(pk)
        self._match = match
        data = match_squads(pk)
        return Response({"data": data, "meta": {"count": len(data["teams"])}})


class MatchFactsView(_MatchCachedView):
    """``GET /matches/{id}/facts`` — toss, officials, venue, result (§3.2)."""

    cache_namespace = "match:facts"

    def get(self, request: Any, pk: str) -> Response:
        match = get_match(pk)
        self._match = match
        return Response({"data": match_facts(pk), "meta": {}})


__all__ = [
    "DeliveriesView",
    "InningsView",
    "MatchDetailView",
    "MatchFactsView",
    "MatchOversView",
    "MatchSquadsView",
    "ScorecardView",
]

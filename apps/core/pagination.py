"""Pagination classes that emit the standard ``{"data", "meta"}`` envelope.

* :class:`CursorPagination` — keyset pagination for time-ordered feeds. The
  cursor encodes ``(sort_key, id)`` so pages stay stable while rows are
  inserted. Malformed cursors are rejected with ``invalid_query``.
* :class:`PageNumberPagination` — offset pagination with a total count, for
  bounded tables (rankings, points tables, leaderboards).
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from typing import Any

from django.db.models import Q
from rest_framework.pagination import BasePagination
from rest_framework.pagination import PageNumberPagination as DrfPageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.errors import InvalidQuery

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class CursorPagination(BasePagination):
    """Keyset ("cursor") pagination ordered by a single field plus the PK.

    Views set :attr:`ordering`, e.g. ``ordering = ("-start_time_utc",)``. The
    primary-key tiebreaker is appended automatically so the page boundary is
    always deterministic.
    """

    page_size = DEFAULT_PAGE_SIZE
    max_page_size = MAX_PAGE_SIZE
    limit_query_param = "limit"
    cursor_query_param = "cursor"
    ordering: Sequence[str] = ("-id",)

    def __init__(self) -> None:
        self.request: Request | None = None
        self.page_size_value = self.page_size
        self.has_more = False
        self.next_cursor: str | None = None

    # -- cursor codec ------------------------------------------------------ #
    def encode_cursor(self, sort_key: Any, pk: Any) -> str:
        """Encode ``(sort_key, id)`` as an opaque, URL-safe string."""
        payload = {"k": sort_key, "id": pk}
        raw = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def decode_cursor(self, cursor: str) -> tuple[Any, Any]:
        """Decode a cursor, rejecting anything malformed with ``invalid_query``."""
        try:
            raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
            payload = json.loads(raw.decode("utf-8"))
            return payload["k"], payload["id"]
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise InvalidQuery("Malformed cursor", details={"cursor": cursor}) from exc

    # -- page size --------------------------------------------------------- #
    def get_page_size(self, request: Request) -> int:
        """Resolve ``?limit=`` (default 25, clamped to 100, integers only)."""
        raw = request.query_params.get(self.limit_query_param)
        if raw is None:
            return self.page_size
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidQuery(
                "limit must be an integer", details={self.limit_query_param: raw}
            ) from exc
        if value < 1:
            raise InvalidQuery("limit must be >= 1", details={self.limit_query_param: raw})
        if self.max_page_size is not None:
            return min(value, self.max_page_size)
        return value

    # -- pagination -------------------------------------------------------- #
    def paginate_queryset(self, queryset: Any, request: Request, view: Any = None) -> list[Any]:
        self.request = request
        self.page_size_value = self.get_page_size(request)

        ordering = list(self.ordering) or ["-pk"]
        first = ordering[0]
        descending = first.startswith("-")
        field = first.lstrip("-+")

        model = queryset.model
        pk_name = model._meta.pk.name

        order_fields = list(ordering)
        tiebreak = f"-{pk_name}" if descending else pk_name
        if tiebreak not in order_fields:
            order_fields.append(tiebreak)

        qs = queryset.order_by(*order_fields)

        cursor = request.query_params.get(self.cursor_query_param)
        if cursor:
            last_key, last_pk = self.decode_cursor(cursor)
            if descending:
                boundary = Q(**{f"{field}__lt": last_key}) | Q(
                    **{field: last_key, f"{pk_name}__lt": last_pk}
                )
            else:
                boundary = Q(**{f"{field}__gt": last_key}) | Q(
                    **{field: last_key, f"{pk_name}__gt": last_pk}
                )
            qs = qs.filter(boundary)

        rows = list(qs[: self.page_size_value + 1])
        self.has_more = len(rows) > self.page_size_value
        if self.has_more:
            rows = rows[: self.page_size_value]
            last = rows[-1]
            self.next_cursor = self.encode_cursor(getattr(last, field), getattr(last, pk_name))
        else:
            self.next_cursor = None
        return rows

    def get_paginated_response(self, data: Sequence[Any]) -> Response:
        return Response(
            {
                "data": list(data),
                "meta": {
                    "limit": self.page_size_value,
                    "has_more": self.has_more,
                    "next_cursor": self.next_cursor,
                },
            }
        )


class PageNumberPagination(DrfPageNumberPagination):
    """Offset pagination with ``page`` / ``page_size`` and a total count."""

    page_size = DEFAULT_PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE

    def get_paginated_response(self, data: Sequence[Any]) -> Response:
        return Response(
            {
                "data": list(data),
                "meta": {
                    "page": self.page.number,  # type: ignore[union-attr]
                    "page_size": self.page.paginator.per_page,  # type: ignore[union-attr]
                    "total_count": self.page.paginator.count,  # type: ignore[union-attr]
                    "total_pages": self.page.paginator.num_pages,  # type: ignore[union-attr]
                    "has_more": self.page.has_next(),  # type: ignore[union-attr]
                },
            }
        )

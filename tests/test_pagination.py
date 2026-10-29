"""Pagination: envelope shape, limits, cursor round-trip & stability."""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.core.errors import InvalidQuery
from apps.core.pagination import CursorPagination, PageNumberPagination

pytestmark = pytest.mark.integration

User = get_user_model()


class _UserCursorPagination(CursorPagination):
    """Concrete cursor paginator ordered by the integer primary key."""

    ordering = ("id",)


rf = APIRequestFactory()


def _request(query: str = "") -> Request:
    return Request(rf.get(f"/{query}"))


def _make_users(count: int) -> list[int]:
    return [User.objects.create_user(username=f"u{i}").pk for i in range(count)]


def test_cursor_default_and_max_page_size() -> None:
    paginator = _UserCursorPagination()
    assert paginator.get_page_size(_request()) == 25
    assert paginator.get_page_size(_request("?limit=10")) == 10
    assert paginator.get_page_size(_request("?limit=1000")) == 100  # clamped to max


def test_cursor_rejects_non_integer_limit() -> None:
    with pytest.raises(InvalidQuery):
        _UserCursorPagination().get_page_size(_request("?limit=abc"))


def test_cursor_round_trips_sort_key_and_id() -> None:
    paginator = _UserCursorPagination()
    cursor = paginator.encode_cursor(5, 42)
    assert paginator.decode_cursor(cursor) == (5, 42)


def test_cursor_rejects_malformed_cursor() -> None:
    paginator = _UserCursorPagination()
    with pytest.raises(InvalidQuery):
        paginator.decode_cursor("@@@ not base64 @@@")


def test_cursor_rejects_cursor_missing_keys() -> None:
    import base64
    import json

    bad = base64.urlsafe_b64encode(json.dumps({"nope": 1}).encode()).decode()
    with pytest.raises(InvalidQuery):
        _UserCursorPagination().decode_cursor(bad)


def test_cursor_paginates_and_returns_envelope_with_next_cursor(db) -> None:
    _make_users(30)
    paginator = _UserCursorPagination()
    queryset = User.objects.all()
    page = paginator.paginate_queryset(queryset, _request("?limit=10"))
    assert len(page) == 10
    response = paginator.get_paginated_response(page)
    body = response.data
    assert set(body) == {"data", "meta"}
    assert body["meta"]["limit"] == 10
    assert body["meta"]["has_more"] is True
    assert body["meta"]["next_cursor"]


def test_cursor_is_stable_when_rows_are_inserted_between_pages(db) -> None:
    _make_users(30)
    qs = User.objects.all()

    first = _UserCursorPagination()
    page1 = first.paginate_queryset(qs, _request("?limit=10"))
    cursor = first.next_cursor

    # Insert a new row *after* page 1 was served.
    User.objects.create_user(username="late")

    second = _UserCursorPagination()
    page2 = second.paginate_queryset(queryset=qs, request=_request(f"?limit=10&cursor={cursor}"))

    ids1 = {user.pk for user in page1}
    ids2 = {user.pk for user in page2}
    assert not (ids1 & ids2), "cursor pagination must not repeat rows"
    assert min(ids2) > max(ids1), "cursor pagination must not skip backward"


def test_cursor_final_page_has_no_next_cursor(db) -> None:
    _make_users(5)
    paginator = _UserCursorPagination()
    page = paginator.paginate_queryset(User.objects.all(), _request("?limit=10"))
    assert len(page) == 5
    assert paginator.next_cursor is None
    assert paginator.get_paginated_response(page).data["meta"]["has_more"] is False


def test_page_number_pagination_includes_total_count(db) -> None:
    _make_users(30)
    paginator = PageNumberPagination()
    page = paginator.paginate_queryset(User.objects.all(), _request("?page=2&page_size=10"))
    assert len(page) == 10
    body = paginator.get_paginated_response(page).data
    assert body["meta"]["page"] == 2
    assert body["meta"]["page_size"] == 10
    assert body["meta"]["total_count"] == 30
    assert body["meta"]["total_pages"] == 3
    assert body["meta"]["has_more"] is True


def test_page_number_default_size_and_max(db) -> None:
    _make_users(30)
    paginator = PageNumberPagination()
    page = paginator.paginate_queryset(User.objects.all(), _request("?page_size=1000"))
    assert len(page) == 30  # capped, only 30 rows exist


def test_cursor_accepts_uuid_sort_values() -> None:
    paginator = _UserCursorPagination()
    sort_value = uuid.uuid4()
    pk = uuid.uuid4()
    cursor = paginator.encode_cursor(sort_value, pk)
    decoded_sort, decoded_pk = paginator.decode_cursor(cursor)
    assert decoded_sort == str(sort_value)
    assert decoded_pk == str(pk)

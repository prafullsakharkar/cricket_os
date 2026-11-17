"""Unit tests for core/http.py cache helpers (API_SPECIFICATION §3.9).

Strong ETag computed from the serialized payload, Cache-Control with optional
stale-while-revalidate, and If-None-Match -> 304 with preserved cache headers.
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.test import override_settings
from rest_framework.test import APIClient

from apps.core.http import apply_cache_policy, compute_etag

pytestmark = pytest.mark.unit

TEST_URLS = "tests.http_urls"


def test_compute_etag_is_strong_and_stable() -> None:
    a = compute_etag(b'{"data": 1}')
    b = compute_etag(b'{"data": 1}')
    c = compute_etag(b'{"data": 2}')
    assert a == b
    assert a.startswith('"') and a.endswith('"')  # strong form
    assert a != c


def test_compute_etag_accepts_str() -> None:
    assert compute_etag('{"data": 1}') == compute_etag(b'{"data": 1}')


def test_apply_cache_policy_sets_headers() -> None:
    response = HttpResponse(b"payload")
    out = apply_cache_policy(response, max_age=600, swr=600, etag='"abc"')
    assert out["Cache-Control"] == "public, max-age=600, stale-while-revalidate=600"
    assert out["ETag"] == '"abc"'
    assert out.status_code == 200
    assert out.content == b"payload"


def test_apply_cache_policy_without_swr() -> None:
    out = apply_cache_policy(HttpResponse(b"p"), max_age=300, etag='"e"')
    assert out["Cache-Control"] == "public, max-age=300"


def test_if_none_match_matching_etag_returns_304() -> None:
    from django.test import RequestFactory

    request = RequestFactory().get("/x", HTTP_IF_NONE_MATCH='"abc"')
    out = apply_cache_policy(HttpResponse(b"payload"), max_age=600, etag='"abc"', request=request)
    assert out.status_code == 304
    assert out.content == b""
    assert out["Cache-Control"] == "public, max-age=600"
    assert out["ETag"] == '"abc"'


def test_if_none_match_mismatch_keeps_200() -> None:
    from django.test import RequestFactory

    request = RequestFactory().get("/x", HTTP_IF_NONE_MATCH='"old"')
    out = apply_cache_policy(HttpResponse(b"payload"), max_age=600, etag='"new"', request=request)
    assert out.status_code == 200
    assert out.content == b"payload"


@override_settings(ROOT_URLCONF=TEST_URLS)
def test_mixin_adds_cache_headers_and_strong_etag() -> None:
    client = APIClient()
    response = client.get("/cached/")
    assert response.status_code == 200
    assert response["Cache-Control"] == "public, max-age=300, stale-while-revalidate=600"
    assert response["ETag"].startswith('"')


@override_settings(ROOT_URLCONF=TEST_URLS)
def test_mixin_304_on_matching_if_none_match() -> None:
    client = APIClient()
    first = client.get("/cached/")
    etag = first["ETag"]
    second = client.get("/cached/", HTTP_IF_NONE_MATCH=etag)
    assert second.status_code == 304
    assert second.content == b""
    assert second["Cache-Control"] == first["Cache-Control"]
    assert second["ETag"] == etag


@override_settings(ROOT_URLCONF=TEST_URLS)
def test_mixin_without_ttl_leaves_headers_alone() -> None:
    client = APIClient()
    response = client.get("/uncached/")
    assert response.status_code == 200
    assert "Cache-Control" not in response
    assert "ETag" not in response

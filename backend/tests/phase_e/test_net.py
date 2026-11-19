"""Trusted-proxy client IP extraction (SECURITY §2 trust model).

``X-Forwarded-For`` is client-controlled; the client IP may only be derived
from it when the request genuinely came through the configured number of
trusted proxy hops. The contract (documented on :func:`client_ip`):

* ``hops=0`` (default): ``X-Forwarded-For`` is ignored entirely — the peer we
  actually saw (``REMOTE_ADDR``) is the client;
* ``hops=N``: the *rightmost* N entries are assumed added by our edge; the
  entry immediately to their left is the client. A header with ≤ N entries (a
  direct client forging the chain) falls back to ``REMOTE_ADDR``; a malformed
  entry is treated the same way.

This mirrors the Django/ipware ``NUM_PROXIES`` model and gives the guarantee
that a client cannot impersonate a hop it did not traverse.
"""

from __future__ import annotations

from django.test import override_settings

from apps.core.net import client_ip


class _Request:
    def __init__(self, meta: dict[str, str]) -> None:
        self.META = meta


def test_no_trusted_proxies_ignores_forwarded_for() -> None:
    request = _Request({"REMOTE_ADDR": "1.2.3.4", "HTTP_X_FORWARDED_FOR": "9.9.9.9"})
    assert client_ip(request, hops=0) == "1.2.3.4"


def test_single_trusted_hop_reads_client_from_chain() -> None:
    # client -> our edge proxy (10.0.0.2) -> us; the edge appended the client.
    request = _Request({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "203.0.113.5, 10.0.0.2"})
    assert client_ip(request, hops=1) == "203.0.113.5"


def test_direct_client_cannot_spoof_with_short_chain() -> None:
    # A direct client's forged header is shorter than the trusted chain would
    # produce, so it is ignored and the real peer is used.
    request = _Request({"REMOTE_ADDR": "1.2.3.4", "HTTP_X_FORWARDED_FOR": "9.9.9.9"})
    assert client_ip(request, hops=1) == "1.2.3.4"


def test_two_trusted_hops() -> None:
    request = _Request(
        {
            "REMOTE_ADDR": "10.0.0.1",
            "HTTP_X_FORWARDED_FOR": "203.0.113.5, 10.0.0.2, 10.0.0.1",
        }
    )
    assert client_ip(request, hops=2) == "203.0.113.5"


def test_malformed_entry_falls_back_to_remote() -> None:
    request = _Request({"REMOTE_ADDR": "1.2.3.4", "HTTP_X_FORWARDED_FOR": "evil, 10.0.0.2"})
    assert client_ip(request, hops=1) == "1.2.3.4"


def test_ipv6_entries_are_supported() -> None:
    request = _Request({"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "2001:db8::5, 10.0.0.2"})
    assert client_ip(request, hops=1) == "2001:db8::5"


def test_uses_the_configured_default_hops() -> None:
    with override_settings(TRUSTED_PROXY_HOPS=2):
        request = _Request(
            {
                "REMOTE_ADDR": "10.0.0.1",
                "HTTP_X_FORWARDED_FOR": "203.0.113.5, 10.0.0.2, 10.0.0.1",
            }
        )
        assert client_ip(request) == "203.0.113.5"


def test_missing_remote_addr_reports_unknown() -> None:
    request = _Request({"HTTP_X_FORWARDED_FOR": "1.2.3.4"})
    assert client_ip(request, hops=1) == "unknown"


def test_whitespace_and_extra_commas_are_tolerated() -> None:
    request = _Request(
        {"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": " 203.0.113.5 ,  10.0.0.2 "}
    )
    assert client_ip(request, hops=1) == "203.0.113.5"

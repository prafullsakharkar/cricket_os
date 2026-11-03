"""HMAC-authenticated push receiver (DATA_INGESTION §2, SECURITY §3).

The webhook is an **ingestion surface**, not part of the public read-only API:

* feature-flagged (``LIVE_PUSH_RECEIVER_ENABLED``, default off);
* authenticated with an HMAC-SHA256 signature over the raw body
  (``X-Signature``) plus a timestamp (``X-Timestamp``) checked against a
  replay window — constant-time comparison, pluggable secret;
* rate-limited per source IP;
* returns ``202`` fast after **enqueueing** the payload for the same validated
  pipeline; the payload is processed by the Celery task, never inline.

The pure verification functions are unit-tested directly; the view wires them
to Django.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Sequence
from typing import Any, ClassVar

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View

HEADER_SIGNATURE = "HTTP_X_SIGNATURE"
HEADER_TIMESTAMP = "HTTP_X_TIMESTAMP"


def compute_signature(payload: bytes, secret: str) -> str:
    """Hex HMAC-SHA256 of ``payload`` keyed with ``secret``."""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Constant-time signature check (``hmac.compare_digest``)."""
    if not secret:
        return False
    expected = compute_signature(payload, secret)
    return hmac.compare_digest(expected, signature)


def verify_timestamp(
    timestamp: str | int | float,
    *,
    now: float | None = None,
    window_seconds: int | None = None,
) -> bool:
    """True when ``timestamp`` is within the replay window of ``now``."""
    window = (
        window_seconds
        if window_seconds is not None
        else int(getattr(settings, "LIVE_PUSH_REPLAY_WINDOW_SECONDS", 300))
    )
    now = now if now is not None else time.time()
    try:
        ts = float(timestamp)
    except TypeError, ValueError:
        return False
    return abs(now - ts) <= window


def _rate_limited(ip: str, *, limit: int = 60, window: int = 60) -> bool:
    """True when ``ip`` has exceeded the push rate limit."""
    key = f"live:push:rl:{ip}"
    count = cache.get(key, 0)
    if count >= limit:
        return True
    if count == 0:
        cache.set(key, 1, timeout=window)
    else:
        cache.incr(key)
    return False


class LivePushReceiverView(View):
    """POST /ingest/live/push — authenticates and enqueues a live payload."""

    # Matches django-stubs' View.http_method_names (an instance attribute), so
    # this is a plain override, not a ClassVar. A tuple keeps ruff's RUF012
    # (mutable class default) happy and works identically in View.as_view().
    http_method_names: Sequence[str] = ("post",)

    #: Overridable in tests; production uses the Celery task.
    enqueue: ClassVar[Any] = None

    def post(self, request: HttpRequest) -> HttpResponse:
        if not getattr(settings, "LIVE_PUSH_RECEIVER_ENABLED", False):
            return HttpResponse(status=404)
        secret = getattr(settings, "LIVE_PUSH_HMAC_SECRET", "")
        if not secret:
            return JsonResponse({"error": "receiver not configured"}, status=503)

        ip = request.META.get("REMOTE_ADDR", "unknown")
        if _rate_limited(ip):
            return JsonResponse({"error": "rate_limited"}, status=429)

        signature = request.META.get(HEADER_SIGNATURE, "")
        timestamp = request.META.get(HEADER_TIMESTAMP, "")
        if not verify_timestamp(timestamp):
            return JsonResponse({"error": "stale_timestamp"}, status=401)
        if not verify_signature(request.body, signature, secret):
            return JsonResponse({"error": "bad_signature"}, status=401)

        enqueue = self.enqueue or _default_enqueue
        enqueue(request.body)
        return JsonResponse({"accepted": True}, status=202)


def _default_enqueue(payload: bytes) -> None:
    from apps.live.services.tasks import receive_push

    receive_push.delay(payload)


__all__ = [
    "LivePushReceiverView",
    "compute_signature",
    "verify_signature",
    "verify_timestamp",
]

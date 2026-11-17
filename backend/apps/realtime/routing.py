"""WebSocket routing for ``apps/realtime`` (API_SPEC §3.8, ledger Group 8).

The SSE transport is a plain Django URL (``apps/realtime/urls.py``); the
WebSocket consumer is a Channels route registered here and mounted in
``config/asgi.py``. The ``pk`` pattern mirrors Django's UUID path converter so
a non-UUID id rejects at routing time exactly like the SSE route's
``<uuid:pk>``.
"""

from __future__ import annotations

from django.urls import re_path

from apps.realtime.consumers import MatchFeedConsumer
from apps.realtime.security import UUID_RE

app_name = "realtime"

websocket_urlpatterns = [
    re_path(
        rf"^api/v1/ws/matches/(?P<pk>{UUID_RE})/$",
        MatchFeedConsumer.as_asgi(),
        name="ws-match-feed",
    ),
]

__all__ = ["websocket_urlpatterns"]

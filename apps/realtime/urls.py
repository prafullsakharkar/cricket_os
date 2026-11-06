"""URL routes under ``/api/v1/`` contributed by the realtime app.

Only the HTTP-side transports live here (SSE). The WebSocket consumer is a
Channels route, registered in ``config/asgi.py`` (Group 8).
"""

from __future__ import annotations

from django.urls import path

from apps.realtime.sse import SseMatchFeed

app_name = "realtime"

urlpatterns = [
    path("sse/matches/<uuid:pk>/", SseMatchFeed.as_view(), name="sse-match-feed"),
]

"""Realtime fan-out: hub, Redis broker, frames and wiring (Phase E Group 6).

The package depends only on ``apps.core`` (import-linter enforced): the durable
event log and the licensing policy are injected through :mod:`apps.realtime.wiring`
by the composition root (``config/asgi.py``). Transports (SSE, WebSocket) are
added in Groups 7/8 on top of :mod:`apps.realtime.hub`.
"""

from __future__ import annotations

default_app_config = "apps.realtime.apps.RealtimeConfig"

__all__ = ["default_app_config"]

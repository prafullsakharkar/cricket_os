"""ASGI entrypoint: HTTP via Django, realtime wiring, and the realtime routes.

This module is the composition root for Phase E realtime (ledger conflict 1):
it imports the concrete implementations from ``apps.matches`` and injects them
into ``apps.realtime.wiring`` so the realtime package itself only ever imports
``apps.core`` (import-linter enforced). The hub/broker/transports are built
here so ASGI workers share one configured instance.

The ``lifespan`` scope is handled explicitly: a worker builds the hub + broker
at startup and runs the broker (Redis pub/sub fan-in, with degraded polling)
for the life of the process, stopping both on shutdown. Without this, the
SSE/WS transports would fail with "realtime hub is not configured".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# Build the Django ASGI app before importing anything that touches the ORM.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.realtime.routing import websocket_urlpatterns  # noqa: E402

logger = logging.getLogger("config.asgi")


def build_realtime_components() -> tuple[Any, Any, Any]:
    """Construct (hub, broker, wiring) with the injected matches implementations.

    Returns the hub and broker so transports (Groups 7/8) can attach; kept
    callable so tests exercise the composition without a second ASGI app. The
    concrete types are imported lazily (they touch the ORM), so the signature
    is intentionally loose.
    """
    from apps.matches.services.stream_reader import (
        LicensingMatchPolicy,
        PostgresEventLogReader,
    )
    from apps.realtime import wiring
    from apps.realtime.broker import Broker
    from apps.realtime.hub import Hub

    hub = Hub()
    wiring.configure(
        reader=PostgresEventLogReader(),
        policy=LicensingMatchPolicy(),
        hub=hub,
    )
    broker = Broker(hub)
    return hub, broker, wiring


class _RealtimeLifespan:
    """Build the realtime components at startup and run the broker.

    ``factory`` is injectable so tests can substitute fakes. On shutdown the
    broker is stopped (its ``run`` loop's ``finally`` stops the hub's ticker).
    """

    def __init__(self, factory: Any | None = None) -> None:
        self._factory = factory or build_realtime_components
        self._hub: Any = None
        self._broker: Any = None
        self._task: asyncio.Task[Any] | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "lifespan.startup":
                try:
                    hub, broker, _ = self._factory()
                except Exception:
                    logger.exception("realtime components failed to build")
                    await send({"type": "lifespan.startup.failed"})
                    return
                self._hub, self._broker = hub, broker
                self._task = asyncio.create_task(broker.run())
                await send({"type": "lifespan.startup.complete"})
            elif message_type == "lifespan.shutdown":
                if self._broker is not None:
                    await self._broker.stop()
                if self._task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await self._task
                    self._task = None
                await send({"type": "lifespan.shutdown.complete"})
                return


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # Group 8: the realtime WebSocket consumer (SSE rides plain HTTP).
        "websocket": URLRouter(websocket_urlpatterns),
        # Group 9: build + run the realtime components for the worker's life.
        "lifespan": _RealtimeLifespan(),
    }
)

"""ASGI entrypoint: HTTP via Django, realtime wiring, and a Channels routing stub.

This module is the composition root for Phase E realtime (ledger conflict 1):
it imports the concrete implementations from ``apps.matches`` and injects them
into ``apps.realtime.wiring`` so the realtime package itself only ever imports
``apps.core`` (import-linter enforced). The hub/broker/transports are built
here so ASGI workers share one configured instance.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# Build the Django ASGI app before importing anything that touches the ORM.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402


def build_realtime_components() -> tuple[object, object, object]:
    """Construct (hub, broker, wiring) with the injected matches implementations.

    Returns the hub and broker so transports (Groups 7/8) can attach; kept
    callable so tests exercise the composition without a second ASGI app.
    """
    from apps.matches.services.stream_reader import (
        LicensingMatchPolicy,
        PostgresEventLogReader,
    )
    from apps.realtime import wiring
    from apps.realtime.broker import Broker
    from apps.realtime.hub import Hub

    wiring.configure(reader=PostgresEventLogReader(), policy=LicensingMatchPolicy())
    hub = Hub()
    broker = Broker(hub)
    return hub, broker, wiring


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # Phase D/E: replace the empty router with real consumer patterns, e.g.
        #   from apps.realtime.routing import websocket_urlpatterns
        #   "websocket": URLRouter(websocket_urlpatterns),
        "websocket": URLRouter([]),
    }
)

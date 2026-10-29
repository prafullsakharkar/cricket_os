"""ASGI entrypoint: HTTP via Django, plus an empty Channels routing stub.

Phase D/E will add websocket consumers to the ``websocket`` branch without
restructuring this module.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# Build the Django ASGI app before importing anything that touches the ORM.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # Phase D/E: replace the empty router with real consumer patterns, e.g.
        #   from apps.realtime.routing import websocket_urlpatterns
        #   "websocket": URLRouter(websocket_urlpatterns),
        "websocket": URLRouter([]),
    }
)

"""ASGI application: HTTP scope via Django plus an (empty) Channels stub."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_asgi_application_routes_http_and_websocket() -> None:
    from channels.routing import ProtocolTypeRouter

    from config.asgi import application

    assert isinstance(application, ProtocolTypeRouter)
    mapping = application.application_mapping
    assert "http" in mapping
    assert "websocket" in mapping

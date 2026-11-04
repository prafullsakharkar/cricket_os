"""App configuration for ``realtime``.

The realtime app owns no domain models: the hub and broker are in-process
services wired at the composition root. ``ready()`` only ensures the realtime
metrics are registered (they are declared eagerly in ``apps.core.metrics``, so
there is nothing to subscribe here) — the app exists to give the module a
settings hook and a home in the import-linter graph.
"""

from __future__ import annotations

from django.apps import AppConfig


class RealtimeConfig(AppConfig):
    """WebSocket/SSE fan-out hub (ARCHITECTURE §2: depends only on core)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.realtime"
    label = "realtime"
    verbose_name = "Realtime"

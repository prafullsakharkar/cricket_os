"""App configuration for the ``search`` module (Phase F Group 6).

The search app has no tables of its own: it reads the domain tables through
their GIN trigram indexes. Nothing to register in ``ready``.
"""

from __future__ import annotations

from django.apps import AppConfig


class SearchConfig(AppConfig):
    name = "apps.search"
    label = "search"
    verbose_name = "search"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        pass

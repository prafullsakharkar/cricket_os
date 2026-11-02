"""App configuration for the ``ingestion`` module."""

from __future__ import annotations

from django.apps import AppConfig


class IngestionConfig(AppConfig):
    name = "apps.ingestion"
    label = "ingestion"
    verbose_name = "ingestion"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        # Import the provider adapters so their pipeline factories are
        # registered for the management commands and Celery tasks. Guarded so
        # repeated ready() calls (tests) do not re-import.
        if getattr(self, "_ready_done", False):
            return
        from apps.ingestion.adapters import cricsheet, summary_cards  # noqa: F401

        self._ready_done = True

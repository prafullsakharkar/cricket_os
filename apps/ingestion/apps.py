"""App configuration for the ``ingestion`` module."""

from __future__ import annotations

from django.apps import AppConfig


class IngestionConfig(AppConfig):
    name = "apps.ingestion"
    label = "ingestion"
    verbose_name = "ingestion"
    default_auto_field = "django.db.models.UUIDField"

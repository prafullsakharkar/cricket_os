"""App configuration for the ``innings`` module."""

from __future__ import annotations

from django.apps import AppConfig


class InningsConfig(AppConfig):
    name = "apps.innings"
    label = "innings"
    verbose_name = "innings"
    default_auto_field = "django.db.models.UUIDField"

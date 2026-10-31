"""App configuration for the administration module."""

from __future__ import annotations

from django.apps import AppConfig


class AdministrationConfig(AppConfig):
    name = "apps.administration"
    label = "administration"
    verbose_name = "Administration"
    default_auto_field = "django.db.models.UUIDField"

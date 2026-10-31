"""App config for the test-only dummy app."""

from __future__ import annotations

from django.apps import AppConfig


class DummyAppConfig(AppConfig):
    name = "tests.dummy_app"
    label = "dummy_app"
    verbose_name = "Dummy (test-only)"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        # Prove merge_players' reference registry is extensible: a third-party
        # app registers its own Player FK without touching apps.players.
        if getattr(self, "_ready_done", False):
            return
        from apps.players.services.merge import register_player_reference
        from tests.dummy_app.models import DummyPlayerRef

        register_player_reference(DummyPlayerRef, "player")
        self._ready_done = True

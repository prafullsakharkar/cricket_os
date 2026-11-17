"""One model: a Player FK registered via ``register_player_reference``.

``merge_players`` re-points every registered reference; this model exists so
tests can prove an app outside the core six is honoured too.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import UUIDPrimaryKeyModel


class DummyPlayerRef(UUIDPrimaryKeyModel):
    player = models.ForeignKey(
        "players.Player", on_delete=models.PROTECT, related_name="dummy_refs"
    )
    label = models.TextField(blank=True, default="")

    class Meta:
        db_table = "dummy_player_ref"

    def __str__(self) -> str:
        return f"DummyPlayerRef({self.label or self.pk})"


__all__ = ["DummyPlayerRef"]

"""Player merge service (Group 4).

``merge_players`` permanently supersedes a duplicate player: every reference
to the loser — provider-map rows, every registered ``Player`` FK, and any
``PlayerMerge`` rows that point at it as canonical — is re-pointed at the
winner, the loser is soft-deleted, and a ``PlayerMerge`` row records the
redirect (reads of the loser's id resolve to the winner: a 301).

**No unmerge.** A merge is irreversible; that is why ``resolve`` never
auto-merges and why the identity thresholds are conservative (see
docs/IDENTITY_RESOLUTION.md §6).

Reference re-pointing is extensible: any model with a ``Player`` FK may call
:func:`register_player_reference` from its own ``ready()``. Phase B registers
``TeamMembership.player`` and ``SquadMember.player`` (in ``teams.apps``); the
test-only ``tests.dummy_app`` app registers its own FK to prove a third-party
app is honoured without touching ``apps.players``.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from django.db import models, transaction
from django.utils import timezone

from apps.core.errors import Conflict
from apps.core.events import TOPIC_PLAYER_MERGED, publish
from apps.players.models import Player, PlayerMerge
from apps.providers.models import ProviderEntityMap

#: (model, field_name) pairs whose Player FK must be re-pointed on merge.
_player_references: list[tuple[type[models.Model], str]] = []


def register_player_reference[T: models.Model](model: type[T], field_name: str) -> None:
    """Register ``model.field_name`` (a Player FK) for merge re-pointing."""
    _player_references.append((cast(type[models.Model], model), field_name))


def _as_uuid(value: object) -> UUID | None:
    """``value`` as a UUID when it is one (else ``None`` — store a label only)."""
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _resolve_canonical(player: Player) -> Player:
    """Follow ``PlayerMerge`` redirects to the player's canonical row.

    A corrupt redirect cycle is refused rather than looped.
    """
    seen: set[UUID] = set()
    current = player
    while True:
        if current.pk in seen:
            raise Conflict("PlayerMerge redirect chain contains a cycle")
        seen.add(current.pk)
        redirect = PlayerMerge.objects.filter(merged_player_id=current.pk).first()
        if redirect is None:
            return current
        current = redirect.canonical_player


def merge_players(
    winner: Player,
    loser: Player,
    *,
    actor: object,
    reason: str | None = None,
) -> PlayerMerge:
    """Merge ``loser`` into ``winner``; returns the ``PlayerMerge`` record.

    - Redirect chains are resolved first: merging into a player that was
      itself merged follows the redirect.
    - ``winner == loser`` and would-be cycles raise :class:`Conflict`.
    - Retrying an already-merged loser is a no-op returning the existing row.
    - The whole merge (re-points + record + soft-delete + audit publish) is one
      atomic block: any failure rolls everything back, so no partial merge and
      no audit row ever outlive the change.
    """
    # Idempotent on retry / already-merged loser: return the existing record.
    existing = PlayerMerge.objects.filter(merged_player_id=loser.pk).first()
    if existing is not None:
        return existing

    winner = _resolve_canonical(winner)
    loser = _resolve_canonical(loser)
    if winner.pk == loser.pk:
        raise Conflict("Cannot merge a player into itself or its merge target")

    with transaction.atomic():
        # 1. Re-point provider map rows.
        ProviderEntityMap.objects.filter(internal_id=loser.pk).update(internal_id=winner.pk)
        # 2. Re-point every registered Player reference. The registry is typed
        # as base ``Model`` (django-stubs attaches ``objects`` only to concrete
        # subclasses), so cast to Any for the dynamic field lookups.
        for model, field_name in _player_references:
            managed = cast(Any, model)
            managed.objects.filter(**{f"{field_name}_id": loser.pk}).update(
                **{f"{field_name}_id": winner.pk}
            )
        # 2b. Carry earlier merges whose canonical was the loser forward.
        PlayerMerge.objects.filter(canonical_player_id=loser.pk).update(
            canonical_player_id=winner.pk
        )
        # 3. Record the redirect.
        merge = PlayerMerge.objects.create(
            canonical_player=winner,
            merged_player=loser,
            reason=reason,
            merged_by=_as_uuid(actor),
        )
        # 4. Soft-delete the loser (reads 301 to the winner).
        loser.is_active = False
        loser.deleted_at = timezone.now()
        loser.save(update_fields=["is_active", "deleted_at"])
        # 5. Audit synchronously: commits or rolls back with the merge.
        publish(
            TOPIC_PLAYER_MERGED,
            {
                "canonical_player_id": str(winner.pk),
                "merged_player_id": str(loser.pk),
                "actor": str(actor),
            },
            on_commit=False,
        )
    return merge


__all__ = ["merge_players", "register_player_reference"]

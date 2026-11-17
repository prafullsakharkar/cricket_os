"""Team-membership temporality selector.

A player belongs to a team between ``joined_at`` (inclusive) and ``left_at``
(exclusive); an open-ended (never-left) membership has a NULL ``left_at``. The
same player may therefore appear twice in the full history (re-join) while at
most one row is active on any given ``as_of`` date.
"""

from __future__ import annotations

import datetime
from uuid import UUID

from django.db.models import Q, QuerySet

from apps.players.models import Player
from apps.teams.models import TeamMembership


def list_players_of_team(team_id: UUID, *, as_of: datetime.date | None = None) -> QuerySet[Player]:
    """Players of ``team_id`` active on ``as_of`` (default: currently active).

    ``as_of`` semantics: ``joined_at <= as_of < left_at``. Without ``as_of``
    only open (never-left) memberships are returned.
    """
    qs = TeamMembership.objects.filter(team_id=team_id)
    if as_of is not None:
        qs = qs.filter(joined_at__lte=as_of).filter(Q(left_at__isnull=True) | Q(left_at__gt=as_of))
    else:
        qs = qs.filter(left_at__isnull=True)
    player_ids = qs.values_list("player_id", flat=True)
    return Player.objects.filter(pk__in=player_ids).order_by("full_name")


__all__ = ["list_players_of_team"]

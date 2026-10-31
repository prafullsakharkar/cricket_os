"""Team, membership, squad and squad-member entities.

Columns and constraint/index names match CRICKET_DATABASE_SCHEMA §3.3/§3.4
exactly (team_membership's open-membership unique index uses
``coalesce(left_at, 'infinity')``). Per decision D1 no timestamps beyond what
the DDL lists; ``source_provider_id`` is a plain UUID column (D2).

The circular Player reference is resolved with lazy string FKs — this module
never imports ``apps.players.models`` (import-linter contract).
"""

from __future__ import annotations

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models.functions import Coalesce

from apps.core.enums import TeamType
from apps.core.expressions import RawColumnExpression
from apps.core.models import UUIDPrimaryKeyModel

_MEMBERSHIP_TYPES = ("squad", "guest", "captain", "coach")
_SQUAD_MEMBER_STATUSES = ("announced", "withdrawn", "replaced")
_GENDERS = ("M", "W", "X")


class Team(UUIDPrimaryKeyModel):
    name = models.TextField()
    short_name = models.TextField(null=True, blank=True)
    abbreviation = models.TextField(null=True, blank=True)
    # Enum-like column; the DDL type is `text` (manifest reports enums as text).
    team_type = models.TextField(choices=TeamType.choices)
    # db_index=False: the reference DDL declares no index on these FKs.
    country = models.ForeignKey(
        "venues.Country",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="teams",
        db_index=False,
    )
    governing_body = models.ForeignKey(
        "venues.GoverningBody",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="teams",
        db_index=False,
    )
    home_venue = models.ForeignKey(
        "venues.Venue",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="home_teams",
        db_index=False,
    )
    gender = models.CharField(max_length=1, default="M")
    is_international = models.BooleanField(default=False)
    logo_url = models.TextField(null=True, blank=True)
    provider_team_id = models.TextField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "team"
        constraints = [
            models.UniqueConstraint(
                fields=["source_provider_id", "provider_team_id"],
                condition=models.Q(provider_team_id__isnull=False),
                name="ux_team_provider",
            ),
            models.CheckConstraint(
                condition=models.Q(team_type__in=TeamType.values),
                name="ck_team_team_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(gender__in=_GENDERS), name="ck_team_gender_valid"
            ),
        ]
        indexes = [
            models.Index(fields=["team_type"], name="ix_team_type"),
            GinIndex(fields=["name"], opclasses=["gin_trgm_ops"], name="ix_team_name_trgm"),
        ]

    def __str__(self) -> str:
        return self.name


class TeamMembership(UUIDPrimaryKeyModel):
    # db_index=False: ix_membership_player covers player_id; team_id has none.
    team = models.ForeignKey(
        "teams.Team", on_delete=models.PROTECT, related_name="memberships", db_index=False
    )
    player = models.ForeignKey(
        "players.Player", on_delete=models.PROTECT, related_name="memberships", db_index=False
    )
    joined_at = models.DateField(null=True, blank=True)
    left_at = models.DateField(null=True, blank=True)
    # Enum-like column; the DDL type is `text`.
    membership_type = models.TextField(default="squad")
    is_captain = models.BooleanField(default=False)
    is_primary = models.BooleanField(default=False)
    source_provider_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "team_membership"
        constraints = [
            # One open (never-left) membership per (team, player); a player may
            # re-join after leaving because the closed rows carry distinct
            # left_at dates while the open row coalesces to 'infinity'::date
            # (rendered raw — Django cannot prepare the Postgres literal).
            models.UniqueConstraint(
                "team_id",
                "player_id",
                Coalesce(
                    "left_at",
                    RawColumnExpression("'infinity'::date", output_field=models.DateField()),
                ),
                name="ux_membership_open",
            ),
            models.CheckConstraint(
                condition=models.Q(membership_type__in=_MEMBERSHIP_TYPES),
                name="ck_team_membership_membership_type_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["player_id"], name="ix_membership_player"),
        ]

    def __str__(self) -> str:
        return f"{self.team} — {self.player}"


class Squad(UUIDPrimaryKeyModel):
    # db_index=False: ix_squad_team / ix_squad_series declared explicitly;
    # the DDL declares no index on season_id.
    team = models.ForeignKey(
        "teams.Team", on_delete=models.PROTECT, related_name="squads", db_index=False
    )
    series = models.ForeignKey(
        "competitions.Series",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="squads",
        db_index=False,
    )
    season = models.ForeignKey(
        "competitions.Season",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="squads",
        db_index=False,
    )
    name = models.TextField(null=True, blank=True)
    announced_at = models.DateTimeField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "squad"
        indexes = [
            models.Index(fields=["team_id"], name="ix_squad_team"),
            models.Index(fields=["series_id"], name="ix_squad_series"),
        ]

    def __str__(self) -> str:
        return self.name or f"{self.team} squad"


class SquadMember(UUIDPrimaryKeyModel):
    # db_index=False: ux_squad_member already covers (squad_id, player_id).
    squad = models.ForeignKey(
        "teams.Squad", on_delete=models.PROTECT, related_name="members", db_index=False
    )
    player = models.ForeignKey(
        "players.Player", on_delete=models.PROTECT, related_name="squad_memberships", db_index=False
    )
    role = models.TextField(null=True, blank=True)
    is_captain = models.BooleanField(default=False)
    is_keeper = models.BooleanField(default=False)
    is_substitute = models.BooleanField(default=False)
    status = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "squad_member"
        constraints = [
            models.UniqueConstraint(fields=["squad", "player"], name="ux_squad_member"),
            models.CheckConstraint(
                condition=models.Q(status__in=_SQUAD_MEMBER_STATUSES),
                name="ck_squad_member_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.player} in {self.squad}"


__all__ = ["Squad", "SquadMember", "Team", "TeamMembership"]

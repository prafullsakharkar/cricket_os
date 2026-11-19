"""Player, PlayerMerge and PlayerDuplicateCandidate entities.

Columns and constraint/index names match CRICKET_DATABASE_SCHEMA §3.3 plus the
Phase B ``player_duplicate_candidate`` table (DDL added to the schema doc in
the same change). ``player.name_key`` is a persisted GeneratedField rendered
by :class:`~apps.players.expressions.NameKeyExpression` exactly as the DDL
writes it (IMMUTABLE integer day-number encoding of the date).

The circular Team reference is resolved with a lazy string FK — this module
never imports ``apps.teams.models`` (import-linter contract).
"""

from __future__ import annotations

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import F, Q

from apps.core.enums import BatStyle, BowlStyle, PlayerRole
from apps.core.models import UUIDPrimaryKeyModel
from apps.players.expressions import NameKeyExpression

_GENDERS = ("M", "W", "X")
_DUPLICATE_STATUSES = ("open", "resolved", "merged")


class Player(UUIDPrimaryKeyModel):
    full_name = models.TextField()
    known_as = models.TextField(null=True, blank=True)
    unique_name = models.TextField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    date_of_death = models.DateField(null=True, blank=True)
    # db_index=False: the DDL declares no index on birth_country_id.
    birth_country = models.ForeignKey(
        "venues.Country",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="players",
        db_index=False,
    )
    birth_place = models.TextField(null=True, blank=True)
    gender = models.CharField(max_length=1, default="M")
    height_cm = models.IntegerField(null=True, blank=True)
    # Enum-like columns; the DDL types are `text`.
    batting_style = models.TextField(choices=BatStyle.choices, null=True, blank=True)
    bowling_style = models.TextField(choices=BowlStyle.choices, null=True, blank=True)
    primary_role = models.TextField(choices=PlayerRole.choices, null=True, blank=True)
    is_wicketkeeper = models.BooleanField(default=False)
    # db_index=False: ix_player_intl_team is declared explicitly below.
    international_team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="players",
        db_index=False,
    )
    face_image_url = models.TextField(null=True, blank=True)
    bio = models.TextField(null=True, blank=True)
    provider_player_id = models.TextField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    # De-dup key rendered exactly as the DDL writes it (see
    # apps/players/expressions.py); every function is IMMUTABLE.
    name_key = models.GeneratedField(
        expression=NameKeyExpression(),
        output_field=models.TextField(),
        db_persist=True,
    )

    class Meta:
        db_table = "player"
        constraints = [
            models.UniqueConstraint(
                fields=["source_provider_id", "provider_player_id"],
                condition=Q(provider_player_id__isnull=False),
                name="ux_player_provider",
            ),
            models.CheckConstraint(condition=Q(gender__in=_GENDERS), name="ck_player_gender_valid"),
            models.CheckConstraint(
                condition=Q(batting_style__in=BatStyle.values),
                name="ck_player_batting_style_valid",
            ),
            models.CheckConstraint(
                condition=Q(bowling_style__in=BowlStyle.values),
                name="ck_player_bowl_style_valid",
            ),
            models.CheckConstraint(
                condition=Q(primary_role__in=PlayerRole.values),
                name="ck_player_primary_role_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["date_of_birth"], name="ix_player_dob"),
            models.Index(fields=["international_team_id"], name="ix_player_intl_team"),
            GinIndex(fields=["full_name"], opclasses=["gin_trgm_ops"], name="ix_player_name_trgm"),
        ]

    def __str__(self) -> str:
        return self.full_name


class PlayerMerge(UUIDPrimaryKeyModel):
    """Merge audit + redirect map.

    ``merged_player`` is superseded by ``canonical_player``; every reference to
    the merged player is re-pointed to the canonical one and lookups redirect
    through this table. There is **no unmerge** (documented in
    docs/IDENTITY_RESOLUTION.md).
    """

    # db_index=False: ix_player_merge_canonical is declared explicitly below;
    # the DDL declares no index on merged_player_id.
    canonical_player = models.ForeignKey(
        "players.Player", on_delete=models.PROTECT, related_name="+", db_index=False
    )
    merged_player = models.ForeignKey(
        "players.Player", on_delete=models.PROTECT, related_name="+", db_index=False
    )
    reason = models.TextField(null=True, blank=True)
    merged_by = models.UUIDField(null=True, blank=True)
    merged_at = models.DateTimeField(default=models.functions.Now)

    class Meta:
        db_table = "player_merge"
        constraints = [
            models.UniqueConstraint(fields=["merged_player"], name="ux_player_merge"),
            models.CheckConstraint(
                condition=~Q(canonical_player_id=F("merged_player_id")),
                name="ck_player_merge_distinct",
            ),
        ]
        indexes = [
            models.Index(fields=["canonical_player_id"], name="ix_player_merge_canonical"),
        ]
        permissions = [("merge_players", "Can merge duplicate players")]

    def __str__(self) -> str:
        return f"{self.merged_player} -> {self.canonical_player}"


class PlayerDuplicateCandidate(UUIDPrimaryKeyModel):
    """A scored (candidate, plausible-match) pair awaiting steward review."""

    # db_index=False: ix_dup_candidate_canonical is declared explicitly below;
    # the DDL declares no index on candidate_player_id.
    candidate_player = models.ForeignKey(
        "players.Player", on_delete=models.PROTECT, related_name="+", db_index=False
    )
    canonical_player = models.ForeignKey(
        "players.Player", on_delete=models.PROTECT, related_name="+", db_index=False
    )
    score = models.DecimalField(max_digits=4, decimal_places=3)
    feature_breakdown = models.JSONField()
    # Enum-like column; the DDL type is `text`.
    status = models.TextField(default="open")
    reviewed_by = models.UUIDField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "player_duplicate_candidate"
        constraints = [
            models.UniqueConstraint(
                fields=["candidate_player", "canonical_player"],
                name="ux_dup_candidate",
            ),
            models.CheckConstraint(
                condition=Q(status__in=_DUPLICATE_STATUSES),
                name="ck_player_duplicate_candidate_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="ix_dup_candidate_status"),
            models.Index(fields=["canonical_player_id"], name="ix_dup_candidate_canonical"),
        ]

    def __str__(self) -> str:
        return f"{self.candidate_player} ~ {self.canonical_player} ({self.score})"


__all__ = ["Player", "PlayerDuplicateCandidate", "PlayerMerge"]

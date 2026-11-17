"""Ball-by-ball facts: ``Over`` and the partitioned ``Delivery`` table.

``Over`` is a normal managed table (CRICKET_DATABASE_SCHEMA §3.6).

``Delivery`` is RANGE-partitioned by ``match_date``. Django cannot create
partitioned tables natively, so the table is created by a hand-written
``RunSQL`` migration and mapped here with an **unmanaged-style** model
(``Meta.managed = False``). The primary key is the composite
``(id, match_date)`` — Django 6.1 supports ``models.CompositePrimaryKey`` — and
every unique index includes ``match_date`` exactly as the revised DDL
specifies. There are **no FKs pointing INTO ``delivery``**; references stay
logical and service-enforced. No ``DEFAULT`` partition exists: partitions are
created on demand by ``deliveries.services.partitions.ensure_partition(year)``
(see the module docstring there for why).

The four generated columns (``total_runs``, ``is_wide``, ``is_noball``,
``is_legal``) are reproduced as ``GeneratedField`` for model fidelity; the real
DDL lives in the migration.
"""

from __future__ import annotations

from django.db import models

from apps.core.enums import DismissalType, RetirementType
from apps.core.models import UUIDPrimaryKeyModel
from apps.core.uuid7 import uuid7


class Over(UUIDPrimaryKeyModel):
    innings = models.ForeignKey(
        "innings.Innings",
        on_delete=models.PROTECT,
        related_name="overs",
        db_index=False,
    )
    over_number = models.IntegerField()
    bowler = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="overs",
        db_index=False,
    )
    is_maiden = models.BooleanField(default=False)
    runs_conceded = models.IntegerField(default=0)
    wickets = models.IntegerField(default=0)
    is_complete = models.BooleanField(default=False)
    deliveries_count = models.IntegerField(default=0)

    class Meta:
        db_table = "over"
        constraints = [
            models.UniqueConstraint(fields=["innings_id", "over_number"], name="ux_over"),
        ]

    def __str__(self) -> str:
        return f"{self.innings_id} over {self.over_number}"


class Delivery(models.Model):
    """One recorded ball (partitioned by ``match_date``; unmanaged table)."""

    id = models.UUIDField(default=uuid7, editable=False)
    match_date = models.DateField()
    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="deliveries",
        db_index=False,
    )
    innings = models.ForeignKey(
        "innings.Innings",
        on_delete=models.PROTECT,
        related_name="deliveries",
        db_index=False,
    )
    over_number = models.IntegerField()
    ball_in_over = models.IntegerField()
    delivery_seq = models.BigIntegerField()
    bowler = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="bowled_deliveries",
        db_index=False,
    )
    batter = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="batted_deliveries",
        db_index=False,
    )
    non_striker = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="non_striker_deliveries",
        db_index=False,
    )
    runs_off_bat = models.IntegerField(default=0)
    wides = models.IntegerField(default=0)
    noballs = models.IntegerField(default=0)
    byes = models.IntegerField(default=0)
    leg_byes = models.IntegerField(default=0)
    penalty_runs = models.IntegerField(default=0)
    is_wicket = models.BooleanField(default=False)
    dismissal_type = models.TextField(choices=DismissalType.choices, null=True, blank=True)
    dismissed_player = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="dismissed_deliveries",
        db_index=False,
    )
    bowler_credited_wicket = models.BooleanField(default=False)
    fielder1 = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="delivery_fielders_1",
        db_index=False,
    )
    fielder2 = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="delivery_fielders_2",
        db_index=False,
    )
    fielder3 = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="delivery_fielders_3",
        db_index=False,
    )
    shot_type = models.TextField(null=True, blank=True)
    commentary_text = models.TextField(null=True, blank=True)
    provider_delivery_id = models.TextField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)
    revision = models.IntegerField(default=1)
    is_current = models.BooleanField(default=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    retired_player = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="retirement_deliveries",
        db_index=False,
    )
    retirement_type = models.TextField(choices=RetirementType.choices, null=True, blank=True)
    total_runs = models.GeneratedField(
        expression=(
            models.F("runs_off_bat")
            + models.F("wides")
            + models.F("noballs")
            + models.F("byes")
            + models.F("leg_byes")
            + models.F("penalty_runs")
        ),
        output_field=models.IntegerField(),
        db_persist=True,
    )
    is_wide = models.GeneratedField(
        expression=models.Case(
            models.When(wides__gt=0, then=models.Value(True)),
            default=models.Value(False),
            output_field=models.BooleanField(),
        ),
        output_field=models.BooleanField(),
        db_persist=True,
    )
    is_noball = models.GeneratedField(
        expression=models.Case(
            models.When(noballs__gt=0, then=models.Value(True)),
            default=models.Value(False),
            output_field=models.BooleanField(),
        ),
        output_field=models.BooleanField(),
        db_persist=True,
    )
    is_legal = models.GeneratedField(
        expression=models.Case(
            models.When(wides=0, noballs=0, then=models.Value(True)),
            default=models.Value(False),
            output_field=models.BooleanField(),
        ),
        output_field=models.BooleanField(),
        db_persist=True,
    )

    pk = models.CompositePrimaryKey("id", "match_date")

    class Meta:
        db_table = "delivery"
        managed = False
        constraints = [
            # (is_wicket AND dismissal_type AND dismissed_player_id) OR
            # (NOT is_wicket AND dismissal_type IS NULL)
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_wicket=True,
                        dismissal_type__isnull=False,
                        dismissed_player_id__isnull=False,
                    )
                    | models.Q(is_wicket=False, dismissal_type__isnull=True)
                ),
                name="ck_delivery_wicket",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(retired_player_id__isnull=True, retirement_type__isnull=True)
                    | models.Q(retired_player_id__isnull=False, retirement_type__isnull=False)
                ),
                name="ck_delivery_retirement",
            ),
            models.CheckConstraint(
                condition=models.Q(runs_off_bat__gte=0), name="ck_delivery_runs_off_bat_nonneg"
            ),
            models.CheckConstraint(
                condition=models.Q(wides__gte=0), name="ck_delivery_wides_nonneg"
            ),
            models.CheckConstraint(
                condition=models.Q(noballs__gte=0), name="ck_delivery_noballs_nonneg"
            ),
            models.CheckConstraint(condition=models.Q(byes__gte=0), name="ck_delivery_byes_nonneg"),
            models.CheckConstraint(
                condition=models.Q(leg_byes__gte=0), name="ck_delivery_leg_byes_nonneg"
            ),
            models.CheckConstraint(
                condition=models.Q(penalty_runs__gte=0), name="ck_delivery_penalty_runs_nonneg"
            ),
            # Exactly one CURRENT row per ball.
            models.UniqueConstraint(
                fields=["match_date", "innings_id", "delivery_seq"],
                condition=models.Q(is_current=True),
                name="ux_delivery_current",
            ),
            models.UniqueConstraint(
                fields=["match_date", "innings_id", "delivery_seq", "revision"],
                name="ux_delivery_revision",
            ),
            models.UniqueConstraint(
                fields=["match_date", "source_provider_id", "provider_delivery_id", "revision"],
                condition=models.Q(provider_delivery_id__isnull=False),
                name="ux_delivery_provider",
            ),
        ]
        indexes = [
            models.Index(fields=["match_date", "match_id"], name="ix_delivery_match"),
            models.Index(fields=["batter_id"], name="ix_delivery_batter"),
            models.Index(fields=["bowler_id"], name="ix_delivery_bowler"),
        ]

    def __str__(self) -> str:
        return f"{self.innings_id} seq {self.delivery_seq}"

"""Innings lifecycle, innings-level events and the innings summary.

Columns and constraint/index names match CRICKET_DATABASE_SCHEMA §3.6 exactly.
``InningsEvent`` carries retirements that are NOT tied to a ball (during a
break, before the first ball, or in an innings that never resumes); a
retirement that coincides with a delivery lives on the ``delivery`` row
(``retired_player_id``/``retirement_type``) and is never duplicated here.
``anchor_delivery_seq`` is the delivery immediately AFTER which the event takes
effect (NULL = before the first delivery).
"""

from __future__ import annotations

from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.enums import InningsEventType, InningsStatus
from apps.core.expressions import RawColumnExpression
from apps.core.models import UUIDPrimaryKeyModel

_ZERO_UUID = "'00000000-0000-0000-0000-000000000000'::uuid"


class Innings(UUIDPrimaryKeyModel):
    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="innings",
        db_index=False,
    )
    innings_number = models.SmallIntegerField()
    batting_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="batting_innings",
        db_index=False,
    )
    bowling_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="bowling_innings",
        db_index=False,
    )
    is_super_over = models.BooleanField(default=False)
    super_over_number = models.SmallIntegerField(null=True, blank=True)
    declared = models.BooleanField(default=False)
    forfeited = models.BooleanField(default=False)
    all_out = models.BooleanField(default=False)
    target = models.IntegerField(null=True, blank=True)
    target_balls = models.IntegerField(null=True, blank=True)
    dls_target = models.IntegerField(null=True, blank=True)
    is_follow_on_innings = models.BooleanField(default=False)
    status = models.TextField(choices=InningsStatus.choices, default=InningsStatus.NOT_STARTED)
    provider_innings_id = models.TextField(null=True, blank=True)
    revision = models.IntegerField(default=1)

    class Meta:
        db_table = "innings"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(innings_number__range=(1, 4)),
                name="ck_innings_innings_number_valid",
            ),
            models.UniqueConstraint(
                "match_id",
                "innings_number",
                "is_super_over",
                Coalesce("super_over_number", 0),
                name="ux_innings",
            ),
        ]
        indexes = [
            models.Index(fields=["match_id"], name="ix_innings_match"),
        ]

    def __str__(self) -> str:
        return f"{self.match_id} innings {self.innings_number}"


class InningsEvent(UUIDPrimaryKeyModel):
    innings = models.ForeignKey(
        "innings.Innings",
        on_delete=models.PROTECT,
        related_name="events",
        db_index=False,
    )
    event_type = models.TextField(choices=InningsEventType.choices)
    player = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="innings_events",
        db_index=False,
    )
    anchor_delivery_seq = models.BigIntegerField(null=True, blank=True)
    over_number = models.IntegerField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    announced_at = models.DateTimeField(null=True, blank=True)
    provider_event_id = models.TextField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)
    revision = models.IntegerField(default=1)
    is_current = models.BooleanField(default=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "innings_event"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(event_type="OTHER") | models.Q(player__isnull=False),
                name="ck_innings_event_player",
            ),
            models.UniqueConstraint(
                "innings_id",
                "event_type",
                Coalesce(
                    "player_id",
                    RawColumnExpression(_ZERO_UUID, output_field=models.UUIDField()),
                ),
                Coalesce("anchor_delivery_seq", -1),
                condition=models.Q(is_current=True),
                name="ux_innings_event_current",
            ),
            models.UniqueConstraint(
                "innings_id",
                "event_type",
                Coalesce(
                    "player_id",
                    RawColumnExpression(_ZERO_UUID, output_field=models.UUIDField()),
                ),
                Coalesce("anchor_delivery_seq", -1),
                "revision",
                name="ux_innings_event_revision",
            ),
            models.UniqueConstraint(
                fields=["source_provider_id", "provider_event_id", "revision"],
                condition=models.Q(provider_event_id__isnull=False),
                name="ux_innings_event_provider",
            ),
        ]
        indexes = [
            models.Index(fields=["innings_id"], name="ix_innings_event_innings"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.player_id} @ {self.anchor_delivery_seq}"


class InningsSummary(models.Model):
    innings = models.OneToOneField(
        "innings.Innings",
        on_delete=models.PROTECT,
        related_name="summary",
        primary_key=True,
    )
    runs = models.IntegerField(default=0)
    wickets = models.IntegerField(default=0)
    legal_balls = models.IntegerField(default=0)
    overs_display = models.TextField(null=True, blank=True)
    extras = models.JSONField(default=dict)
    run_rate = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    batch_complete = models.BooleanField(default=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "innings_summary"

    def __str__(self) -> str:
        return f"summary {self.innings_id}: {self.runs}/{self.wickets}"

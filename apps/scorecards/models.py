"""Derived scorecard read models: batting/bowling entries, FOW, partnerships.

These tables are **derived read models** (CRICKET_DATABASE_SCHEMA §3.6 / §4.9):
they are rebuilt from ``delivery`` + ``innings_event`` by the scoring engine
via ``scorecards.services.rebuild``. They are never written directly by the
delivery write path. Columns and constraint/index names match the DDL exactly.
"""

from __future__ import annotations

from django.db import models

from apps.core.enums import DismissalType
from apps.core.models import UUIDPrimaryKeyModel


class BattingEntry(UUIDPrimaryKeyModel):
    innings = models.ForeignKey(
        "innings.Innings",
        on_delete=models.PROTECT,
        related_name="batting_entries",
        db_index=False,
    )
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="batting_entries",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="batting_entries",
        db_index=False,
    )
    position = models.IntegerField(null=True, blank=True)
    is_captain = models.BooleanField(default=False)
    is_keeper = models.BooleanField(default=False)
    runs = models.IntegerField(default=0)
    balls = models.IntegerField(default=0)
    dots = models.IntegerField(default=0)
    fours = models.IntegerField(default=0)
    sixes = models.IntegerField(default=0)
    ones = models.IntegerField(default=0)
    twos = models.IntegerField(default=0)
    threes = models.IntegerField(default=0)
    fives = models.IntegerField(default=0)
    minutes = models.IntegerField(null=True, blank=True)
    strike_rate = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    out_desc = models.TextField(null=True, blank=True)
    is_out = models.BooleanField(default=False)
    dismissal_type = models.TextField(choices=DismissalType.choices, null=True, blank=True)
    bowler = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="bowler_batting_entries",
        db_index=False,
    )
    fielder1 = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="catcher_batting_entries",
        db_index=False,
    )
    fielder2 = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fielder2_batting_entries",
        db_index=False,
    )
    fielder3 = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fielder3_batting_entries",
        db_index=False,
    )
    wicket_code = models.TextField(null=True, blank=True)
    is_overseas = models.BooleanField(default=False)
    revision = models.IntegerField(default=1)

    class Meta:
        db_table = "batting_entry"
        constraints = [
            models.UniqueConstraint(fields=["innings_id", "player_id"], name="ux_batting_entry"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} {self.runs} in {self.innings_id}"


class BowlingEntry(UUIDPrimaryKeyModel):
    innings = models.ForeignKey(
        "innings.Innings",
        on_delete=models.PROTECT,
        related_name="bowling_entries",
        db_index=False,
    )
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="bowling_entries",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="bowling_entries",
        db_index=False,
    )
    overs_display = models.TextField(null=True, blank=True)
    legal_balls = models.IntegerField(default=0)
    maidens = models.IntegerField(default=0)
    runs_conceded = models.IntegerField(default=0)
    wickets = models.IntegerField(default=0)
    wides = models.IntegerField(default=0)
    noballs = models.IntegerField(default=0)
    dots = models.IntegerField(default=0)
    economy = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    bowling_avg = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    strike_rate = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    revision = models.IntegerField(default=1)

    class Meta:
        db_table = "bowling_entry"
        constraints = [
            models.UniqueConstraint(fields=["innings_id", "player_id"], name="ux_bowling_entry"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} {self.wickets}/{self.runs_conceded} in {self.innings_id}"


class FallOfWicket(UUIDPrimaryKeyModel):
    innings = models.ForeignKey(
        "innings.Innings",
        on_delete=models.PROTECT,
        related_name="fall_of_wickets",
        db_index=False,
    )
    wicket_number = models.IntegerField()
    score = models.IntegerField()
    over_display = models.TextField(null=True, blank=True)
    delivery_seq = models.BigIntegerField(null=True, blank=True)
    dismissed_player = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fall_of_wickets",
        db_index=False,
    )

    class Meta:
        db_table = "fall_of_wicket"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(wicket_number__range=(1, 10)),
                name="ck_fow_wicket_number_valid",
            ),
            models.UniqueConstraint(fields=["innings_id", "wicket_number"], name="ux_fow"),
        ]

    def __str__(self) -> str:
        return f"{self.innings_id} #{self.wicket_number} @ {self.score}"


class Partnership(UUIDPrimaryKeyModel):
    innings = models.ForeignKey(
        "innings.Innings",
        on_delete=models.PROTECT,
        related_name="partnerships",
        db_index=False,
    )
    wicket_number = models.IntegerField()
    batter1 = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="partnerships_1",
        db_index=False,
    )
    batter2 = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="partnerships_2",
        db_index=False,
    )
    runs = models.IntegerField(default=0)
    balls = models.IntegerField(default=0)
    fours = models.IntegerField(default=0)
    sixes = models.IntegerField(default=0)
    start_delivery_seq = models.BigIntegerField(null=True, blank=True)
    end_delivery_seq = models.BigIntegerField(null=True, blank=True)
    unbroken = models.BooleanField(default=True)

    class Meta:
        db_table = "partnership"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(wicket_number__range=(0, 10)),
                name="ck_partnership_wicket_number_valid",
            ),
            models.UniqueConstraint(fields=["innings_id", "wicket_number"], name="ux_partnership"),
        ]

    def __str__(self) -> str:
        return f"{self.innings_id} #{self.wicket_number} ({self.runs})"

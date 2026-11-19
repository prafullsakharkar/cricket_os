"""Per-match and aggregate statistics (Phase C scope + Phase F aggregates).

Phase C adds ``player_match_stat``, built from the scoring engine's per-match
lines when a match completes or a correction is applied — never on per-delivery
events (ARCHITECTURE §3, SCORING_RULES §6). Phase F adds the Tier-2 aggregate
tables from DATABASE_SCHEMA §3.7 (``player_career_stat``, ``team_stat``,
``head_to_head``, ``leaderboard``, ``form_trend``, ``tournament_standings``)
plus the ``statistics_watermark`` helper (the brief's recompute-watermark
table, ledger decision 3/10). Every aggregate row carries the brief-required
watermark/labelling columns (``rule_version``, ``inputs_digest``,
``source_kind``, coverage) — added to the doc DDL in the same change so
schema-ddl / migration-drift stay green.

Expression uniques (``ux_career_scope``, ``ux_team_stat_scope``, ``ux_h2h``,
``ux_leaderboard``, ``ux_form_trend``, ``ux_standings``, ``ux_watermark``) are
created by the migration as verbatim ``CREATE UNIQUE INDEX`` statements (the
delivery-table precedent, ledger decision 3), so models stay clean for
``makemigrations --check``.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.enums import FormatType, StatScope
from apps.core.models import UUIDPrimaryKeyModel


class SourceKind(models.TextChoices):
    """What an aggregate row is derived from (the official/derived label)."""

    COMPUTED = "computed", "Computed from imported matches"
    PROVIDER = "provider", "Provider-supplied"


class AggregateWatermarkMixin(models.Model):
    """Brief-required watermark/labelling columns shared by aggregate rows."""

    #: The scoring ``RuleConfig.version`` (or provider version) that produced it.
    rule_version = models.TextField()
    #: Deterministic digest of the source rows + rule version (rebuild-compare).
    inputs_digest = models.TextField()
    #: official/derived label: ``computed`` vs ``provider`` (ledger decision 3).
    source_kind = models.TextField(choices=SourceKind.choices, default=SourceKind.COMPUTED)
    # Coverage: matches counted, how many were partial, first/last match date.
    matches_counted = models.IntegerField(default=0)
    partial_matches = models.IntegerField(default=0)
    first_match_date = models.DateField(null=True, blank=True)
    last_match_date = models.DateField(null=True, blank=True)
    is_partial = models.BooleanField(default=False)

    class Meta:
        abstract = True


class PlayerMatchStat(UUIDPrimaryKeyModel):
    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="player_stats",
        db_index=False,
    )
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="match_stats",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="match_stats",
        db_index=False,
    )
    innings = models.ForeignKey(
        "innings.Innings",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="player_stats",
        db_index=False,
    )
    did_bat = models.BooleanField(default=False)
    did_bowl = models.BooleanField(default=False)
    did_field = models.BooleanField(default=False)
    is_playing_xi = models.BooleanField(default=True)
    batting = models.JSONField(default=dict)
    bowling = models.JSONField(default=dict)
    fielding = models.JSONField(default=dict)
    source_provider_id = models.UUIDField(null=True, blank=True)
    computed_at = models.DateTimeField(default=timezone.now)
    # Phase C additions (see module docstring).
    is_partial = models.BooleanField(default=False)
    revision = models.IntegerField(default=1)

    class Meta:
        db_table = "player_match_stat"
        constraints = [
            models.UniqueConstraint(fields=["match_id", "player_id"], name="ux_player_match_stat"),
        ]
        indexes = [
            models.Index(fields=["player_id"], name="ix_pms_player"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} in {self.match_id}"


class PlayerCareerStat(AggregateWatermarkMixin, UUIDPrimaryKeyModel):
    """Career/format/season/series/team/venue/opposition aggregates (§3.7)."""

    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="career_stats",
        db_index=False,
    )
    scope_type = models.TextField(choices=StatScope.choices)
    scope_ref_id = models.UUIDField(null=True, blank=True)
    format_type = models.TextField(choices=FormatType.choices, null=True, blank=True)
    gender = models.CharField(max_length=1, default="M")
    matches = models.IntegerField(default=0)
    innings = models.IntegerField(default=0)
    not_outs = models.IntegerField(default=0)
    runs = models.IntegerField(default=0)
    balls_faced = models.IntegerField(default=0)
    high_score = models.IntegerField(null=True, blank=True)
    high_score_not_out = models.BooleanField(default=False)
    fifties = models.IntegerField(default=0)
    hundreds = models.IntegerField(default=0)
    fours = models.IntegerField(default=0)
    sixes = models.IntegerField(default=0)
    batting_avg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    strike_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    catches = models.IntegerField(default=0)
    stumpings = models.IntegerField(default=0)
    balls_bowled = models.IntegerField(default=0)
    runs_conceded = models.IntegerField(default=0)
    wickets = models.IntegerField(default=0)
    maidens = models.IntegerField(default=0)
    best_wickets = models.IntegerField(null=True, blank=True)
    best_runs = models.IntegerField(null=True, blank=True)
    five_wicket_hauls = models.IntegerField(default=0)
    bowling_avg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    economy = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    bowling_sr = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    revision = models.IntegerField(default=1)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "player_career_stat"

    def __str__(self) -> str:
        return f"{self.player_id} {self.scope_type} {self.format_type or ''}"


class TeamStat(AggregateWatermarkMixin, UUIDPrimaryKeyModel):
    """Team aggregates per scope/format (§3.7)."""

    team = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="stat_lines",
        db_index=False,
    )
    scope_type = models.TextField(choices=StatScope.choices)
    scope_ref_id = models.UUIDField(null=True, blank=True)
    format_type = models.TextField(choices=FormatType.choices, null=True, blank=True)
    matches = models.IntegerField(default=0)
    won = models.IntegerField(default=0)
    lost = models.IntegerField(default=0)
    tied = models.IntegerField(default=0)
    drawn = models.IntegerField(default=0)
    no_result = models.IntegerField(default=0)
    runs_scored = models.IntegerField(default=0)
    runs_conceded = models.IntegerField(default=0)
    wickets_taken = models.IntegerField(default=0)
    nrr = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    revision = models.IntegerField(default=1)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "team_stat"

    def __str__(self) -> str:
        return f"{self.team_id} {self.scope_type} {self.format_type or ''}"


class HeadToHead(AggregateWatermarkMixin, UUIDPrimaryKeyModel):
    """Head-to-head between two teams, per format (§3.7, ``ck_h2h_order``)."""

    team_low = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="h2h_low",
        db_index=False,
    )
    team_high = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="h2h_high",
        db_index=False,
    )
    format_type = models.TextField(choices=FormatType.choices, null=True, blank=True)
    matches = models.IntegerField(default=0)
    low_wins = models.IntegerField(default=0)
    high_wins = models.IntegerField(default=0)
    ties = models.IntegerField(default=0)
    draws = models.IntegerField(default=0)
    no_results = models.IntegerField(default=0)
    last_match = models.ForeignKey(
        "matches.Match",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=False,
    )
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "head_to_head"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(team_low_id__lt=models.F("team_high_id")),
                name="ck_h2h_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.team_low_id} v {self.team_high_id} {self.format_type or ''}"


class Leaderboard(AggregateWatermarkMixin, UUIDPrimaryKeyModel):
    """Precomputed leaderboard rows (§3.7)."""

    scope_type = models.TextField(choices=StatScope.choices)
    scope_ref_id = models.UUIDField(null=True, blank=True)
    metric = models.TextField()
    format_type = models.TextField(choices=FormatType.choices, null=True, blank=True)
    rank = models.IntegerField()
    player = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="leaderboard_rows",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="leaderboard_rows",
        db_index=False,
    )
    value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    supporting = models.JSONField(null=True, blank=True, default=dict)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "leaderboard"

    def __str__(self) -> str:
        return f"{self.metric} #{self.rank}"


class FormTrend(AggregateWatermarkMixin, UUIDPrimaryKeyModel):
    """Rolling form windows per player/format/metric (§3.7)."""

    player = models.ForeignKey(
        "players.Player",
        on_delete=models.CASCADE,
        related_name="form_trends",
        db_index=False,
    )
    format_type = models.TextField(choices=FormatType.choices, null=True, blank=True)
    window_size = models.IntegerField()
    metric = models.TextField()
    value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    series = models.JSONField(null=True, blank=True, default=list)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "form_trend"

    def __str__(self) -> str:
        return f"{self.player_id} {self.metric} {self.window_size}"


class TournamentStandings(AggregateWatermarkMixin, UUIDPrimaryKeyModel):
    """Points table per series/stage/group (§3.7)."""

    series = models.ForeignKey(
        "competitions.Series",
        on_delete=models.PROTECT,
        related_name="standings_rows",
        db_index=False,
    )
    stage = models.ForeignKey(
        "competitions.MatchStage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="standings_rows",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="standings_rows",
        db_index=False,
    )
    group_name = models.TextField(null=True, blank=True)
    rank = models.IntegerField(null=True, blank=True)
    matches_played = models.IntegerField(default=0)
    matches_won = models.IntegerField(default=0)
    matches_lost = models.IntegerField(default=0)
    matches_tied = models.IntegerField(default=0)
    matches_drawn = models.IntegerField(default=0)
    no_result = models.IntegerField(default=0)
    points = models.IntegerField(default=0)
    nrr = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    form = models.TextField(null=True, blank=True)
    qualify_status = models.TextField(null=True, blank=True)
    no_of_qual = models.IntegerField(null=True, blank=True)
    revision = models.IntegerField(default=1)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "tournament_standings"

    def __str__(self) -> str:
        return f"{self.series_id} #{self.rank} {self.team_id}"


class StatisticsWatermark(UUIDPrimaryKeyModel):
    """Per-match recompute watermark (ledger decision 10).

    Records the source digest the aggregates were computed from and the set of
    aggregate keys this match contributed to, so a correction that changes the
    result or participants recomputes the union of the previous and current
    dependency sets, and the scheduled sweep can find terminal matches whose
    aggregates are behind.
    """

    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.CASCADE,
        related_name="statistics_watermark",
        db_index=False,
    )
    source_digest = models.TextField()
    aggregate_keys = models.JSONField(default=list)
    status = models.TextField(default="done")  # pending|done|failed
    recomputed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "statistics_watermark"
        constraints = [
            models.UniqueConstraint(fields=["match_id"], name="ux_watermark"),
        ]

    def __str__(self) -> str:
        return f"watermark {self.match_id} {self.status}"

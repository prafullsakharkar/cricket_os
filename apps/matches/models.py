"""Match aggregate: match, participation, toss, officials, result, awards.

Columns and constraint/index names match CRICKET_DATABASE_SCHEMA §3.5 exactly.
``source_provider_id`` is a plain UUID column (decision D2); ``squad_id`` on
``MatchPlayer`` is a plain UUID reference (no FK, matching the DDL). FK fields
are declared ``db_index=False`` where the reference DDL declares no index on
them; every index in the DDL is reproduced here with its exact name.

Status-vs-result convention (DOMAIN_MODEL §2.4): ``match.status`` is the
lifecycle (SCHEDULED … COMPLETED/ABANDONED); ``match.result_type`` is the
outcome (WIN/TIE/DRAW/NO_RESULT/…). ``match_result`` is the normalised 1:1
outcome row; the denormalised columns on ``match`` cache the same values for
list projections.
"""

from __future__ import annotations

from django.db import models

from apps.core.enums import FormatType, MatchStatus, ResultType
from apps.core.models import UUIDPrimaryKeyModel

_TOSS_DECISIONS = ("bat", "bowl", "n/a")
_MARGIN_TYPES = ("runs", "wickets", "innings", "tie", "n/a")
_AWARD_TYPES = ("player_of_match", "player_of_series", "batter_of_match", "bowler_of_match")
_PLAYER_GROUP_LABELS = ("playing_xi", "bench", "substitute")
_OFFICIAL_ROLES = (
    "umpire1",
    "umpire2",
    "third_umpire",
    "reserve_umpire",
    "referee",
    "tv_umpire",
)


class Match(UUIDPrimaryKeyModel):
    series = models.ForeignKey(
        "competitions.Series",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="matches",
        db_index=False,
    )
    competition = models.ForeignKey(
        "competitions.Competition",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="matches",
        db_index=False,
    )
    season = models.ForeignKey(
        "competitions.Season",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="matches",
        db_index=False,
    )
    stage = models.ForeignKey(
        "competitions.MatchStage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="matches",
        db_index=False,
    )
    format_type = models.TextField(choices=FormatType.choices)
    match_number = models.IntegerField(null=True, blank=True)
    match_desc = models.TextField(null=True, blank=True)
    venue = models.ForeignKey(
        "venues.Venue",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="matches",
        db_index=False,
    )
    gender = models.CharField(max_length=1, default="M")
    start_time_utc = models.DateTimeField()
    end_time_utc = models.DateTimeField(null=True, blank=True)
    source_local_date = models.DateField()
    source_timezone = models.TextField(null=True, blank=True)
    status = models.TextField(choices=MatchStatus.choices, default=MatchStatus.SCHEDULED)
    status_detail = models.TextField(null=True, blank=True)
    #: Human-readable position in the match calendar/session ("Day 4 - Tea").
    state_title = models.TextField(null=True, blank=True)
    result_type = models.TextField(choices=ResultType.choices, null=True, blank=True)
    result_text = models.TextField(null=True, blank=True)
    toss_winner_team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="toss_won_matches",
        db_index=False,
    )
    toss_decision = models.TextField(null=True, blank=True)
    winner_team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="won_matches",
        db_index=False,
    )
    is_follow_on = models.BooleanField(default=False)
    is_dls_applied = models.BooleanField(default=False)
    dls_par_score = models.JSONField(null=True, blank=True)
    overs_limit = models.IntegerField(null=True, blank=True)
    balls_per_over = models.IntegerField(default=6)
    provider_match_id = models.TextField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)
    provider_updated_at = models.DateTimeField(null=True, blank=True)
    ingested_at = models.DateTimeField(default=models.functions.Now)
    #: Last time live ingestion committed a change for this match. Drives
    #: ``meta.stale`` on read responses (API_SPECIFICATION §3.1/§3.9).
    last_ingested_at = models.DateTimeField(null=True, blank=True)
    revision = models.IntegerField(default=1)

    class Meta:
        db_table = "match"
        constraints = [
            models.UniqueConstraint(
                fields=["source_provider_id", "provider_match_id"],
                condition=models.Q(provider_match_id__isnull=False),
                name="ux_match_provider",
            ),
            models.CheckConstraint(
                condition=models.Q(toss_decision__in=_TOSS_DECISIONS),
                name="ck_match_toss_decision_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(gender__in=("M", "W", "X")),
                name="ck_match_gender_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["-start_time_utc"], name="ix_match_start"),
            models.Index(fields=["status", "-start_time_utc"], name="ix_match_status_start"),
            models.Index(fields=["series_id"], name="ix_match_series"),
            models.Index(fields=["venue_id"], name="ix_match_venue"),
            models.Index(fields=["source_local_date"], name="ix_match_local_date"),
        ]

    def __str__(self) -> str:
        return f"{self.format_type} {self.source_local_date} {self.id}"


class MatchEventLog(UUIDPrimaryKeyModel):
    """Append-only per-match realtime event stream (DOMAIN_MODEL §3.12).

    ``seq`` is a monotonic **per-match** sequence allocated under a Postgres
    advisory transaction lock (``events_log.append_event``), so concurrent
    appends are gapless and a rollback leaves neither an event nor a gap.
    The log is operational/derived: canonical data remains replayable from
    deliveries, and rows are pruned after the post-completion retention window.
    """

    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="event_log",
        db_index=False,
    )
    seq = models.BigIntegerField()
    event_type = models.TextField()
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=models.functions.Now)

    class Meta:
        db_table = "match_event_log"
        constraints = [
            models.UniqueConstraint(fields=["match_id", "seq"], name="ux_match_event_seq"),
        ]
        indexes = [
            models.Index(fields=["match_id", "seq"], name="ix_match_event_log"),
        ]

    def __str__(self) -> str:
        return f"{self.match_id}#{self.seq} {self.event_type}"


class MatchTeam(UUIDPrimaryKeyModel):
    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="teams",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="match_teams",
        db_index=False,
    )
    team_number = models.SmallIntegerField()
    is_home = models.BooleanField(default=False)
    is_neutral_venue = models.BooleanField(default=False)
    provider_team_id = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "match_team"
        constraints = [
            models.UniqueConstraint(fields=["match_id", "team_id"], name="ux_match_team"),
            models.UniqueConstraint(fields=["match_id", "team_number"], name="ux_match_team_num"),
            models.CheckConstraint(
                condition=models.Q(team_number__in=(1, 2)),
                name="ck_match_team_team_number_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.match_id} #{self.team_number} {self.team_id}"


class Toss(UUIDPrimaryKeyModel):
    match = models.OneToOneField(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="toss",
        db_index=False,
    )
    winner_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="tosses_won",
        db_index=False,
    )
    decision = models.TextField()
    toss_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "toss"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(decision__in=("bat", "bowl")),
                name="ck_toss_decision_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"toss {self.match_id} -> {self.decision}"


class MatchPlayer(UUIDPrimaryKeyModel):
    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="players",
        db_index=False,
    )
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="match_players",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        on_delete=models.PROTECT,
        related_name="match_players",
        db_index=False,
    )
    # Plain UUID per DDL (no REFERENCES clause).
    squad_id = models.UUIDField(null=True, blank=True)
    batting_position = models.IntegerField(null=True, blank=True)
    is_captain = models.BooleanField(default=False)
    is_keeper = models.BooleanField(default=False)
    is_overseas = models.BooleanField(default=False)
    is_substitute = models.BooleanField(default=False)
    is_playing_xi = models.BooleanField(default=True)
    playing_xi_change = models.BooleanField(default=False)
    in_match_change = models.BooleanField(default=False)
    group_label = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "match_player"
        constraints = [
            models.UniqueConstraint(fields=["match_id", "player_id"], name="ux_match_player"),
            models.CheckConstraint(
                condition=models.Q(group_label__in=_PLAYER_GROUP_LABELS),
                name="ck_match_player_group_label_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["player_id"], name="ix_match_player_player"),
            models.Index(fields=["match_id", "team_id"], name="ix_match_player_team"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} in {self.match_id}"


class MatchOfficial(UUIDPrimaryKeyModel):
    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="officials",
        db_index=False,
    )
    official_name = models.TextField()
    official_player = models.ForeignKey(
        "players.Player",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="officiated_matches",
        db_index=False,
    )
    role = models.TextField()
    country = models.ForeignKey(
        "venues.Country",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="officials",
        db_index=False,
    )

    class Meta:
        db_table = "match_official"
        constraints = [
            models.UniqueConstraint(fields=["match_id", "role"], name="ux_match_official"),
            models.CheckConstraint(
                condition=models.Q(role__in=_OFFICIAL_ROLES),
                name="ck_match_official_role_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.role}: {self.official_name}"


class MatchResult(UUIDPrimaryKeyModel):
    match = models.OneToOneField(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="result",
        db_index=False,
    )
    winner_team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="results",
        db_index=False,
    )
    result_type = models.TextField(choices=ResultType.choices)
    margin_type = models.TextField(null=True, blank=True)
    margin_value = models.IntegerField(null=True, blank=True)
    result_text = models.TextField(null=True, blank=True)
    is_dls = models.BooleanField(default=False)
    is_super_over = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "match_result"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(margin_type__in=_MARGIN_TYPES),
                name="ck_match_result_margin_type_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.match_id}: {self.result_type}"


class MatchAward(UUIDPrimaryKeyModel):
    match = models.ForeignKey(
        "matches.Match",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="awards",
        db_index=False,
    )
    series = models.ForeignKey(
        "competitions.Series",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="awards",
        db_index=False,
    )
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="awards",
        db_index=False,
    )
    award_type = models.TextField()

    class Meta:
        db_table = "match_award"
        constraints = [
            models.UniqueConstraint(
                fields=["match_id", "award_type"],
                condition=models.Q(match_id__isnull=False),
                name="ux_match_award",
            ),
            models.UniqueConstraint(
                fields=["series_id", "award_type"],
                condition=models.Q(series_id__isnull=False),
                name="ux_series_award",
            ),
            models.CheckConstraint(
                condition=models.Q(award_type__in=_AWARD_TYPES),
                name="ck_match_award_award_type_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.award_type}: {self.player_id}"

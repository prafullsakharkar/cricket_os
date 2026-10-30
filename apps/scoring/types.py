"""Frozen, fully-typed inputs and outputs for the scoring engine.

Identifiers are opaque strings (UUIDs at the persistence boundary, see
SCORING_ENGINE_API.md). Every output carries the ``rule_version`` that produced
it so historical rebuilds remain reproducible. Derived/estimated metrics are
marked with :func:`apps.scoring.classification.derived`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from apps.scoring.classification import Classified, derived
from apps.scoring.errors import (
    InvalidDeliveryError,
    InvalidEventError,
    InvalidInningsError,
)


class Format(StrEnum):
    TEST = "TEST"
    FIRST_CLASS = "FIRST_CLASS"
    ODI = "ODI"
    LIST_A = "LIST_A"
    T20I = "T20I"
    T20 = "T20"
    T10 = "T10"
    THE_HUNDRED = "THE_HUNDRED"
    OTHER = "OTHER"


class MatchStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    TOSS = "TOSS"
    IN_PROGRESS = "IN_PROGRESS"
    INNINGS_BREAK = "INNINGS_BREAK"
    DRINKS = "DRINKS"
    RAIN_DELAY = "RAIN_DELAY"
    BAD_LIGHT = "BAD_LIGHT"
    STUMPS = "STUMPS"
    DAY_BREAK = "DAY_BREAK"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    CANCELLED = "CANCELLED"
    FORFEITED = "FORFEITED"
    POSTPONED = "POSTPONED"


class InningsStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    DECLARED = "DECLARED"
    ALL_OUT = "ALL_OUT"
    COMPLETED = "COMPLETED"
    FORFEITED = "FORFEITED"


class ResultType(StrEnum):
    WIN = "WIN"
    TIE = "TIE"
    DRAW = "DRAW"
    NO_RESULT = "NO_RESULT"
    ABANDONED = "ABANDONED"
    CANCELLED = "CANCELLED"
    FORFEIT = "FORFEIT"
    SUPER_OVER_TIE_BREAK = "SUPER_OVER_TIE_BREAK"


class DismissalType(StrEnum):
    BOWLED = "BOWLED"
    CAUGHT = "CAUGHT"
    CAUGHT_AND_BOWLED = "CAUGHT_AND_BOWLED"
    LBW = "LBW"
    RUN_OUT = "RUN_OUT"
    STUMPED = "STUMPED"
    HIT_WICKET = "HIT_WICKET"
    OBSTRUCTING_THE_FIELD = "OBSTRUCTING_THE_FIELD"
    TIMED_OUT = "TIMED_OUT"
    HANDLED_THE_BALL = "HANDLED_THE_BALL"
    HIT_THE_BALL_TWICE = "HIT_THE_BALL_TWICE"
    RETIRED_OUT = "RETIRED_OUT"
    MANKAD = "MANKAD"


class RetirementType(StrEnum):
    HURT = "HURT"
    NOT_OUT = "NOT_OUT"


class InningsEventType(StrEnum):
    RETIREMENT_HURT = "RETIREMENT_HURT"
    RETIREMENT_NOT_OUT = "RETIREMENT_NOT_OUT"
    RESUME = "RESUME"
    OTHER = "OTHER"


class RetirementSource(StrEnum):
    DELIVERY = "delivery"
    EVENT = "event"


class MarginType(StrEnum):
    RUNS = "runs"
    WICKETS = "wickets"
    INNINGS = "innings"
    TIE = "tie"
    NONE = "n/a"


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    """One canonical, current ball-by-ball row.

    ``is_legal``, ``is_wide``, ``is_noball`` and ``total_runs`` mirror the
    generated columns in DATABASE_SCHEMA §3.6 and are never stored twice.
    """

    innings_id: str
    delivery_seq: int
    over_number: int
    ball_in_over: int
    bowler_id: str
    batter_id: str
    non_striker_id: str
    runs_off_bat: int = 0
    wides: int = 0
    noballs: int = 0
    byes: int = 0
    leg_byes: int = 0
    penalty_runs: int = 0
    is_wicket: bool = False
    dismissal_type: DismissalType | None = None
    dismissed_player_id: str | None = None
    bowler_credited_wicket: bool = False
    fielder1_id: str | None = None
    fielder2_id: str | None = None
    fielder3_id: str | None = None
    retired_player_id: str | None = None
    retirement_type: RetirementType | None = None
    revision: int = 1
    is_current: bool = True

    def __post_init__(self) -> None:
        if self.delivery_seq < 1:
            raise InvalidDeliveryError(
                "delivery_seq must be >= 1",
                delivery_seq=self.delivery_seq,
                innings_id=self.innings_id,
            )
        if self.over_number < 1:
            raise InvalidDeliveryError(
                "over_number must be >= 1",
                delivery_seq=self.delivery_seq,
                innings_id=self.innings_id,
            )
        if self.ball_in_over < 1:
            raise InvalidDeliveryError(
                "ball_in_over must be >= 1",
                delivery_seq=self.delivery_seq,
                innings_id=self.innings_id,
            )
        for name in (
            "runs_off_bat",
            "wides",
            "noballs",
            "byes",
            "leg_byes",
            "penalty_runs",
        ):
            if getattr(self, name) < 0:
                raise InvalidDeliveryError(
                    f"{name} must be >= 0",
                    delivery_seq=self.delivery_seq,
                    innings_id=self.innings_id,
                )
        if self.revision < 1:
            raise InvalidDeliveryError(
                "revision must be >= 1",
                delivery_seq=self.delivery_seq,
                innings_id=self.innings_id,
            )
        if self.is_wicket:
            if self.dismissal_type is None or self.dismissed_player_id is None:
                raise InvalidDeliveryError(
                    "a wicket requires dismissal_type and dismissed_player_id",
                    delivery_seq=self.delivery_seq,
                    innings_id=self.innings_id,
                )
            if self.retirement_type is not None:
                raise InvalidDeliveryError(
                    "a wicket cannot also carry a HURT/NOT_OUT retirement",
                    delivery_seq=self.delivery_seq,
                    innings_id=self.innings_id,
                )
        else:
            if self.dismissal_type is not None or self.dismissed_player_id is not None:
                raise InvalidDeliveryError(
                    "a non-wicket cannot carry dismissal fields",
                    delivery_seq=self.delivery_seq,
                    innings_id=self.innings_id,
                )
            if self.bowler_credited_wicket:
                raise InvalidDeliveryError(
                    "bowler_credited_wicket requires is_wicket",
                    delivery_seq=self.delivery_seq,
                    innings_id=self.innings_id,
                )
        if (self.retired_player_id is None) != (self.retirement_type is None):
            raise InvalidDeliveryError(
                "retired_player_id and retirement_type must both be set or both be null",
                delivery_seq=self.delivery_seq,
                innings_id=self.innings_id,
            )

    @property
    def total_runs(self) -> int:
        return (
            self.runs_off_bat
            + self.wides
            + self.noballs
            + self.byes
            + self.leg_byes
            + self.penalty_runs
        )

    @property
    def extras_component(self) -> int:
        return self.wides + self.noballs + self.byes + self.leg_byes + self.penalty_runs

    @property
    def is_wide(self) -> bool:
        return self.wides > 0

    @property
    def is_noball(self) -> bool:
        return self.noballs > 0

    @property
    def is_legal(self) -> bool:
        return self.wides == 0 and self.noballs == 0


@dataclass(frozen=True, slots=True)
class InningsEvent:
    """A non-ball event; ``anchor_delivery_seq`` is the delivery it follows."""

    innings_id: str
    event_type: InningsEventType
    player_id: str | None = None
    anchor_delivery_seq: int | None = None
    over_number: int | None = None
    revision: int = 1
    is_current: bool = True

    def __post_init__(self) -> None:
        if self.event_type is not InningsEventType.OTHER and self.player_id is None:
            raise InvalidEventError(
                "this event type requires player_id", delivery_seq=self.anchor_delivery_seq
            )
        if self.anchor_delivery_seq is not None and self.anchor_delivery_seq < 1:
            raise InvalidEventError(
                "anchor_delivery_seq must be >= 1 or null",
                delivery_seq=self.anchor_delivery_seq,
            )
        if self.over_number is not None and self.over_number < 1:
            raise InvalidEventError(
                "over_number must be >= 1", delivery_seq=self.anchor_delivery_seq
            )
        if self.revision < 1:
            raise InvalidEventError("revision must be >= 1", delivery_seq=self.anchor_delivery_seq)

    @property
    def is_retirement(self) -> bool:
        return self.event_type in {
            InningsEventType.RETIREMENT_HURT,
            InningsEventType.RETIREMENT_NOT_OUT,
        }

    @property
    def retirement_type(self) -> RetirementType | None:
        if self.event_type is InningsEventType.RETIREMENT_HURT:
            return RetirementType.HURT
        if self.event_type is InningsEventType.RETIREMENT_NOT_OUT:
            return RetirementType.NOT_OUT
        return None


@dataclass(frozen=True, slots=True)
class InningsContext:
    """Innings metadata plus its canonical current deliveries and events."""

    innings_id: str
    innings_number: int
    batting_team_id: str
    bowling_team_id: str
    is_super_over: bool = False
    super_over_number: int | None = None
    declared: bool = False
    forfeited: bool = False
    all_out: bool = False
    target: int | None = None
    target_balls: int | None = None
    dls_target: int | None = None
    is_follow_on_innings: bool = False
    status: InningsStatus = InningsStatus.IN_PROGRESS
    deliveries: tuple[DeliveryRecord, ...] = ()
    events: tuple[InningsEvent, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.innings_number <= 4:
            raise InvalidInningsError(
                "innings_number must be between 1 and 4", innings_id=self.innings_id
            )
        if self.batting_team_id == self.bowling_team_id:
            raise InvalidInningsError(
                "batting and bowling team must differ", innings_id=self.innings_id
            )
        if self.is_super_over and (self.super_over_number is None or self.super_over_number < 1):
            raise InvalidInningsError(
                "a super over requires super_over_number >= 1", innings_id=self.innings_id
            )
        if self.declared and self.all_out:
            raise InvalidInningsError(
                "an innings cannot be both declared and all out", innings_id=self.innings_id
            )
        if self.target is not None and self.target < 0:
            raise InvalidInningsError("target must be >= 0", innings_id=self.innings_id)
        if self.target_balls is not None and self.target_balls < 0:
            raise InvalidInningsError("target_balls must be >= 0", innings_id=self.innings_id)
        if self.dls_target is not None and self.dls_target < 0:
            raise InvalidInningsError("dls_target must be >= 0", innings_id=self.innings_id)
        for delivery in self.deliveries:
            if delivery.innings_id != self.innings_id:
                raise InvalidInningsError(
                    "delivery belongs to another innings",
                    innings_id=self.innings_id,
                    delivery_seq=delivery.delivery_seq,
                )
        for event in self.events:
            if event.innings_id != self.innings_id:
                raise InvalidInningsError(
                    "event belongs to another innings",
                    innings_id=self.innings_id,
                    delivery_seq=event.anchor_delivery_seq,
                )


@dataclass(frozen=True, slots=True)
class MatchContext:
    """Match metadata needed to decide a result."""

    match_id: str
    format: Format
    status: MatchStatus
    teams: tuple[str, str]
    overs_limit: int | None = None
    balls_per_over: int = 6
    is_follow_on: bool = False
    is_dls_applied: bool = False
    time_expired: bool = False
    result_type: ResultType | None = None

    def __post_init__(self) -> None:
        if len(self.teams) != 2 or self.teams[0] == self.teams[1]:
            raise InvalidInningsError("a match requires two distinct teams")
        if self.overs_limit is not None and self.overs_limit < 1:
            raise InvalidInningsError("overs_limit must be >= 1")
        if self.balls_per_over < 1:
            raise InvalidInningsError("balls_per_over must be >= 1")


@dataclass(frozen=True, slots=True)
class ExtrasBreakdown(Classified):
    rule_version: str
    wides: int = 0
    noballs: int = 0
    byes: int = 0
    leg_byes: int = 0
    penalty: int = 0
    total: int = 0


@dataclass(frozen=True, slots=True)
class Totals(Classified):
    rule_version: str
    runs: int = 0
    wickets: int = 0
    legal_balls: int = 0
    extras: ExtrasBreakdown = field(default_factory=lambda: ExtrasBreakdown(rule_version="unknown"))
    overs_display: str = "0.0"
    run_rate: float | None = None
    estimated: bool = field(default=False, metadata=derived())


@dataclass(frozen=True, slots=True)
class BattingLine(Classified):
    rule_version: str
    player_id: str
    did_bat: bool = False
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    ones: int = 0
    twos: int = 0
    threes: int = 0
    fives: int = 0
    dots: int = 0
    out: bool = False
    dismissal_type: DismissalType | None = None
    bowler_id: str | None = None
    fielder1_id: str | None = None
    fielder2_id: str | None = None
    fielder3_id: str | None = None
    retirement_type: RetirementType | None = None
    strike_rate: float | None = None
    dot_pct: float | None = field(default=None, metadata=derived())
    boundary_pct: float | None = field(default=None, metadata=derived())
    estimated: bool = field(default=False, metadata=derived())

    @property
    def is_not_out(self) -> bool:
        return self.did_bat and not self.out


@dataclass(frozen=True, slots=True)
class BowlingLine(Classified):
    rule_version: str
    player_id: str
    legal_balls: int = 0
    runs_conceded: int = 0
    wickets: int = 0
    maidens: int = 0
    wides: int = 0
    noballs: int = 0
    dots: int = 0
    overs_display: str = "0.0"
    economy: float | None = None
    bowling_avg: float | None = None
    strike_rate: float | None = None
    estimated: bool = field(default=False, metadata=derived())


@dataclass(frozen=True, slots=True)
class FallOfWicket(Classified):
    rule_version: str
    wicket_number: int
    score: int
    over_display: str
    delivery_seq: int
    dismissed_player_id: str


@dataclass(frozen=True, slots=True)
class Partnership(Classified):
    rule_version: str
    wicket_number: int
    batter1_id: str | None
    batter2_id: str | None
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    start_delivery_seq: int | None = None
    end_delivery_seq: int | None = None
    unbroken: bool = True


@dataclass(frozen=True, slots=True)
class OverSummary(Classified):
    rule_version: str
    over_number: int
    bowler_id: str
    legal_balls: int = 0
    runs_conceded: int = 0
    wickets: int = 0
    is_maiden: bool = False
    is_complete: bool = False
    deliveries_count: int = 0


@dataclass(frozen=True, slots=True)
class RetirementRecord(Classified):
    rule_version: str
    player_id: str
    kind: RetirementType
    anchor_delivery_seq: int | None
    source: RetirementSource
    resumed: bool = False


@dataclass(frozen=True, slots=True)
class InningsResult(Classified):
    rule_version: str
    innings_id: str
    innings_number: int
    batting_team_id: str
    bowling_team_id: str
    totals: Totals
    batting: tuple[BattingLine, ...] = ()
    bowling: tuple[BowlingLine, ...] = ()
    fall_of_wickets: tuple[FallOfWicket, ...] = ()
    partnerships: tuple[Partnership, ...] = ()
    overs: tuple[OverSummary, ...] = ()
    retirements: tuple[RetirementRecord, ...] = ()
    is_super_over: bool = False
    super_over_number: int | None = None
    is_follow_on_innings: bool = False
    declared: bool = False
    all_out: bool = False
    status: InningsStatus = InningsStatus.COMPLETED
    dls_target: int | None = None
    estimated: bool = field(default=False, metadata=derived())


@dataclass(frozen=True, slots=True)
class MatchResult(Classified):
    rule_version: str
    match_id: str
    result_type: ResultType
    winner_team_id: str | None = None
    margin_type: MarginType = MarginType.NONE
    margin_value: int | None = None
    is_dls: bool = False
    is_super_over: bool = False
    result_text: str | None = None


@dataclass(frozen=True, slots=True)
class MatchScorecard(Classified):
    rule_version: str
    match_id: str
    innings: tuple[InningsResult, ...] = ()
    result: MatchResult | None = None
    estimated: bool = field(default=False, metadata=derived())


@dataclass(frozen=True, slots=True)
class PlayerMatchLine(Classified):
    """Per-match durable batting/bowling facts used by Tier-2 aggregates."""

    rule_version: str
    match_id: str
    player_id: str
    team_id: str
    format: Format
    did_bat: bool = False
    did_bowl: bool = False
    batting: tuple[BattingLine, ...] = ()
    bowling: tuple[BowlingLine, ...] = ()
    is_super_over: bool = False

    @property
    def innings_batted(self) -> int:
        return sum(1 for line in self.batting if line.did_bat)

    @property
    def not_outs(self) -> int:
        return sum(1 for line in self.batting if line.is_not_out)

    @property
    def runs(self) -> int:
        return sum(line.runs for line in self.batting)

    @property
    def wickets(self) -> int:
        return sum(line.wickets for line in self.bowling)


@dataclass(frozen=True, slots=True)
class TeamMatchLine(Classified):
    """One team's innings contribution to a match, for points and NRR."""

    rule_version: str
    match_id: str
    team_id: str
    format: Format
    runs_for: int = 0
    balls_faced: int = 0
    all_out: bool = False
    overs_limit: int | None = None
    runs_against: int = 0
    balls_bowled: int = 0
    opponent_all_out: bool = False
    result_type: ResultType | None = None
    won: bool = False
    is_super_over: bool = False
    is_no_result: bool = False


@dataclass(frozen=True, slots=True)
class SeasonTotals(Classified):
    rule_version: str
    team_id: str
    matches: int = 0
    won: int = 0
    lost: int = 0
    tied: int = 0
    drawn: int = 0
    no_result: int = 0
    points: int = 0
    runs_for: int = 0
    balls_faced: int = 0
    runs_against: int = 0
    balls_bowled: int = 0
    nrr: float | None = None


@dataclass(frozen=True, slots=True)
class StandingsRow(Classified):
    rule_version: str
    position: int
    team_id: str
    played: int = 0
    won: int = 0
    lost: int = 0
    tied: int = 0
    drawn: int = 0
    no_result: int = 0
    points: int = 0
    nrr: float | None = None


@dataclass(frozen=True, slots=True)
class CareerTotals(Classified):
    rule_version: str
    player_id: str
    matches: int = 0
    innings: int = 0
    not_outs: int = 0
    runs: int = 0
    balls_faced: int = 0
    high_score: int | None = None
    high_score_not_out: bool = False
    fifties: int = 0
    hundreds: int = 0
    fours: int = 0
    sixes: int = 0
    batting_avg: float | None = None
    strike_rate: float | None = None
    balls_bowled: int = 0
    runs_conceded: int = 0
    wickets: int = 0
    maidens: int = 0
    best_wickets: int | None = None
    best_runs: int | None = None
    five_wicket_hauls: int = 0
    bowling_avg: float | None = None
    economy: float | None = None
    bowling_sr: float | None = None

"""Fixture builders for the pure scoring tests (no Django, no database)."""

from __future__ import annotations

from collections.abc import Sequence

from apps.scoring.config import RuleConfig, preset_for_format
from apps.scoring.types import (
    DeliveryRecord,
    DismissalType,
    Format,
    InningsContext,
    InningsEvent,
    InningsEventType,
    InningsResult,
    InningsStatus,
    MatchContext,
    MatchStatus,
    RetirementType,
    Totals,
)

INNINGS = "inn-1"
TEAM_A = "team-a"
TEAM_B = "team-b"


def delivery(
    seq: int = 0,
    *,
    delivery_seq: int | None = None,
    batter: str = "bat-1",
    non_striker: str = "bat-2",
    bowler: str = "bowl-1",
    runs: int = 0,
    runs_off_bat: int | None = None,
    wides: int = 0,
    noballs: int = 0,
    byes: int = 0,
    leg_byes: int = 0,
    penalty: int = 0,
    is_wicket: bool = False,
    dismissal_type: DismissalType | None = None,
    dismissed: str | None = None,
    credited: bool = False,
    fielder1: str | None = None,
    fielder2: str | None = None,
    fielder3: str | None = None,
    retired: str | None = None,
    retirement_type: RetirementType | None = None,
    over_number: int | None = None,
    ball_in_over: int | None = None,
    innings_id: str = INNINGS,
    is_current: bool = True,
    revision: int = 1,
) -> DeliveryRecord:
    sequence = seq if delivery_seq is None else delivery_seq
    return DeliveryRecord(
        innings_id=innings_id,
        delivery_seq=sequence,
        over_number=over_number if over_number is not None else (sequence - 1) // 6 + 1,
        ball_in_over=ball_in_over if ball_in_over is not None else (sequence - 1) % 6 + 1,
        bowler_id=bowler,
        batter_id=batter,
        non_striker_id=non_striker,
        runs_off_bat=runs if runs_off_bat is None else runs_off_bat,
        wides=wides,
        noballs=noballs,
        byes=byes,
        leg_byes=leg_byes,
        penalty_runs=penalty,
        is_wicket=is_wicket,
        dismissal_type=dismissal_type,
        dismissed_player_id=dismissed,
        bowler_credited_wicket=credited,
        fielder1_id=fielder1,
        fielder2_id=fielder2,
        fielder3_id=fielder3,
        retired_player_id=retired,
        retirement_type=retirement_type,
        is_current=is_current,
        revision=revision,
    )


def legal_run(seq: int, runs: int = 0, **kwargs: object) -> DeliveryRecord:
    return delivery(seq, runs=runs, **kwargs)  # type: ignore[arg-type]


def event(
    event_type: InningsEventType,
    *,
    player: str | None = "bat-1",
    anchor: int | None = None,
    innings_id: str = INNINGS,
    is_current: bool = True,
) -> InningsEvent:
    return InningsEvent(
        innings_id=innings_id,
        event_type=event_type,
        player_id=player,
        anchor_delivery_seq=anchor,
        is_current=is_current,
    )


def innings(
    deliveries: Sequence[DeliveryRecord] = (),
    events: Sequence[InningsEvent] = (),
    *,
    innings_id: str = INNINGS,
    innings_number: int = 1,
    batting_team: str = TEAM_A,
    bowling_team: str = TEAM_B,
    **kwargs: object,
) -> InningsContext:
    return InningsContext(
        innings_id=innings_id,
        innings_number=innings_number,
        batting_team_id=batting_team,
        bowling_team_id=bowling_team,
        deliveries=tuple(deliveries),
        events=tuple(events),
        **kwargs,  # type: ignore[arg-type]
    )


def match(
    *,
    fmt: Format = Format.ODI,
    status: MatchStatus = MatchStatus.IN_PROGRESS,
    teams: tuple[str, str] = (TEAM_A, TEAM_B),
    overs_limit: int | None = 50,
    balls_per_over: int = 6,
    is_dls_applied: bool = False,
    time_expired: bool = False,
    **kwargs: object,
) -> MatchContext:
    return MatchContext(
        match_id="match-1",
        format=fmt,
        status=status,
        teams=teams,
        overs_limit=overs_limit,
        balls_per_over=balls_per_over,
        is_dls_applied=is_dls_applied,
        time_expired=time_expired,
        **kwargs,  # type: ignore[arg-type]
    )


def result(
    team: str,
    runs: int,
    *,
    wickets: int = 0,
    legal: int = 0,
    all_out: bool = False,
    innings_number: int = 1,
    is_super_over: bool = False,
    super_over_number: int | None = None,
    dls_target: int | None = None,
    rule_version: str = "icc-odi-2024",
    declared: bool = False,
) -> InningsResult:
    bowling_team = TEAM_B if team == TEAM_A else TEAM_A
    return InningsResult(
        rule_version=rule_version,
        innings_id=f"inn-{team}-{innings_number}-{is_super_over}-{super_over_number}",
        innings_number=innings_number,
        batting_team_id=team,
        bowling_team_id=bowling_team,
        totals=Totals(
            rule_version=rule_version,
            runs=runs,
            wickets=wickets,
            legal_balls=legal,
            overs_display="0.0",
        ),
        is_super_over=is_super_over,
        super_over_number=super_over_number,
        all_out=all_out,
        declared=declared,
        status=InningsStatus.COMPLETED,
        dls_target=dls_target,
    )


def config(fmt: Format = Format.ODI) -> RuleConfig:
    return preset_for_format(fmt)

"""Batting/bowling lines, wicket counting and dismissal attribution (E9-E11)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from apps.scoring.config import RuleConfig
from apps.scoring.laws import rates
from apps.scoring.laws.extras import (
    batter_faces_ball,
    bowler_runs_charged,
    is_batter_dot,
    is_bowling_dot,
)
from apps.scoring.laws.overs import maidens_of, overs_display
from apps.scoring.types import (
    BattingLine,
    BowlingLine,
    DeliveryRecord,
    RetirementType,
)


def wicket_count(deliveries: Iterable[DeliveryRecord], config: RuleConfig) -> int:
    """Distinct dismissed players with ``is_wicket`` (RETIRED_OUT included)."""

    dismissed = {
        delivery.dismissed_player_id
        for delivery in deliveries
        if delivery.is_wicket and delivery.dismissed_player_id is not None
    }
    return min(len(dismissed), config.max_wickets)


def is_team_wicket(delivery: DeliveryRecord) -> bool:
    return delivery.is_wicket


def is_bowler_wicket(delivery: DeliveryRecord) -> bool:
    return delivery.is_wicket and delivery.bowler_credited_wicket


def dismissal_for(player_id: str, deliveries: Iterable[DeliveryRecord]) -> DeliveryRecord | None:
    for delivery in deliveries:
        if delivery.is_wicket and delivery.dismissed_player_id == player_id:
            return delivery
    return None


def batter_appearances(deliveries: Sequence[DeliveryRecord]) -> tuple[str, ...]:
    """Players in first-appearance order, including non-strikers at the crease."""

    seen: dict[str, None] = {}
    for delivery in deliveries:
        seen.setdefault(delivery.batter_id, None)
        seen.setdefault(delivery.non_striker_id, None)
    return tuple(seen)


def bowler_appearances(deliveries: Sequence[DeliveryRecord]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for delivery in deliveries:
        seen.setdefault(delivery.bowler_id, None)
    return tuple(seen)


def batting_line(
    player_id: str,
    deliveries: Sequence[DeliveryRecord],
    config: RuleConfig,
    rule_version: str,
    *,
    estimated: bool = False,
) -> BattingLine:
    faced = [
        delivery
        for delivery in deliveries
        if delivery.batter_id == player_id and batter_faces_ball(delivery, config)
    ]
    runs = sum(delivery.runs_off_bat for delivery in faced)
    balls = len(faced)
    fours = sum(1 for delivery in faced if delivery.runs_off_bat == 4)
    sixes = sum(1 for delivery in faced if delivery.runs_off_bat == 6)
    ones = sum(1 for delivery in faced if delivery.runs_off_bat == 1)
    twos = sum(1 for delivery in faced if delivery.runs_off_bat == 2)
    threes = sum(1 for delivery in faced if delivery.runs_off_bat == 3)
    fives = sum(1 for delivery in faced if delivery.runs_off_bat == 5)
    dots = sum(1 for delivery in faced if is_batter_dot(delivery))

    dismissal = dismissal_for(player_id, deliveries)
    retired = next(
        (
            delivery.retirement_type
            for delivery in deliveries
            if delivery.retired_player_id == player_id and delivery.retirement_type is not None
        ),
        None,
    )
    return BattingLine(
        rule_version=rule_version,
        player_id=player_id,
        did_bat=any(
            delivery.batter_id == player_id or delivery.non_striker_id == player_id
            for delivery in deliveries
        ),
        runs=runs,
        balls=balls,
        fours=fours,
        sixes=sixes,
        ones=ones,
        twos=twos,
        threes=threes,
        fives=fives,
        dots=dots,
        out=dismissal is not None,
        dismissal_type=None if dismissal is None else dismissal.dismissal_type,
        bowler_id=None if dismissal is None else dismissal.bowler_id,
        fielder1_id=None if dismissal is None else dismissal.fielder1_id,
        fielder2_id=None if dismissal is None else dismissal.fielder2_id,
        fielder3_id=None if dismissal is None else dismissal.fielder3_id,
        retirement_type=retired,
        strike_rate=rates.strike_rate(runs, balls),
        dot_pct=rates.dot_pct(dots, balls),
        boundary_pct=rates.boundary_pct(fours, sixes, balls),
        estimated=estimated,
    )


def retirement_type_for(
    player_id: str, deliveries: Iterable[DeliveryRecord]
) -> RetirementType | None:
    for delivery in deliveries:
        if delivery.retired_player_id == player_id:
            return delivery.retirement_type
    return None


def bowling_line(
    player_id: str,
    deliveries: Sequence[DeliveryRecord],
    config: RuleConfig,
    rule_version: str,
    *,
    estimated: bool = False,
) -> BowlingLine:
    rows = [delivery for delivery in deliveries if delivery.bowler_id == player_id]
    legal = sum(1 for delivery in rows if delivery.is_legal)
    charged = sum(bowler_runs_charged(delivery) for delivery in rows)
    wickets = sum(1 for delivery in rows if is_bowler_wicket(delivery))
    return BowlingLine(
        rule_version=rule_version,
        player_id=player_id,
        legal_balls=legal,
        runs_conceded=charged,
        wickets=wickets,
        maidens=maidens_of(deliveries, player_id, config, rule_version),
        wides=sum(delivery.wides for delivery in rows),
        noballs=sum(delivery.noballs for delivery in rows),
        dots=sum(1 for delivery in rows if is_bowling_dot(delivery)),
        overs_display=overs_display(legal, config),
        economy=rates.economy(charged, legal, config),
        bowling_avg=rates.bowling_average(charged, wickets),
        strike_rate=rates.bowling_strike_rate(legal, wickets),
        estimated=estimated,
    )

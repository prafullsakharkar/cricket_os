"""Legal-ball counting, over/set completion, overs display and maidens."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from apps.scoring.config import RuleConfig
from apps.scoring.errors import InvalidDeliveryError
from apps.scoring.laws.extras import bowler_runs_charged
from apps.scoring.types import DeliveryRecord, OverSummary


def legal_balls(deliveries: Iterable[DeliveryRecord]) -> int:
    return sum(1 for delivery in deliveries if delivery.is_legal)


def overs_display(legal_ball_count: int, config: RuleConfig) -> str:
    """``full_units.remaining_balls`` in the format's own unit (E25)."""

    if legal_ball_count < 0:
        raise InvalidDeliveryError("legal ball count must be >= 0")
    whole, remainder = divmod(legal_ball_count, config.balls_per_over)
    return f"{whole}.{remainder}"


def over_is_complete(legal_ball_count: int, config: RuleConfig) -> bool:
    return legal_ball_count == config.balls_per_over


def total_balls(config: RuleConfig) -> int | None:
    return config.total_balls


def balls_remaining(total: int | None, legal_ball_count: int) -> int | None:
    if total is None:
        return None
    return max(total - legal_ball_count, 0)


def overs_of(
    deliveries: Sequence[DeliveryRecord], config: RuleConfig, rule_version: str
) -> tuple[OverSummary, ...]:
    """Group deliveries by ``over_number`` and summarise each over/set."""

    grouped: dict[int, list[DeliveryRecord]] = {}
    for delivery in deliveries:
        grouped.setdefault(delivery.over_number, []).append(delivery)

    summaries: list[OverSummary] = []
    for over_number in sorted(grouped):
        rows = grouped[over_number]
        bowlers = {row.bowler_id for row in rows}
        if len(bowlers) != 1:
            raise InvalidDeliveryError(
                "an over/set must have exactly one bowler",
                delivery_seq=rows[0].delivery_seq,
                innings_id=rows[0].innings_id,
            )
        legal = legal_balls(rows)
        charged = sum(bowler_runs_charged(row) for row in rows)
        is_complete = over_is_complete(legal, config)
        summaries.append(
            OverSummary(
                rule_version=rule_version,
                over_number=over_number,
                bowler_id=rows[0].bowler_id,
                legal_balls=legal,
                runs_conceded=charged,
                wickets=sum(1 for row in rows if row.is_wicket and row.bowler_credited_wicket),
                is_maiden=is_complete and charged == 0,
                is_complete=is_complete,
                deliveries_count=len(rows),
            )
        )
    return tuple(summaries)


def maidens_of(
    deliveries: Sequence[DeliveryRecord],
    bowler_id: str,
    config: RuleConfig,
    rule_version: str,
) -> int:
    return sum(
        1
        for over in overs_of(deliveries, config, rule_version)
        if over.bowler_id == bowler_id and over.is_maiden
    )

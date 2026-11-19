"""Tournament standings: points, NRR ordering and deterministic tie-breaks."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from apps.scoring.aggregates import season_totals
from apps.scoring.config import RuleConfig
from apps.scoring.types import StandingsRow, TeamMatchLine


def build_standings(
    team_ids: Sequence[str],
    lines: Iterable[TeamMatchLine],
    config: RuleConfig,
) -> tuple[StandingsRow, ...]:
    """Rank teams by points, then NRR, then stable team id (E14, §2.11)."""

    rows = [season_totals(team_id, lines, config) for team_id in dict.fromkeys(team_ids)]
    rows.sort(
        key=lambda row: (
            -row.points,
            -(row.nrr if row.nrr is not None else float("-inf")),
            row.team_id,
        )
    )
    return tuple(
        StandingsRow(
            rule_version=config.version,
            position=position,
            team_id=row.team_id,
            played=row.matches,
            won=row.won,
            lost=row.lost,
            tied=row.tied,
            drawn=row.drawn,
            no_result=row.no_result,
            points=row.points,
            nrr=row.nrr,
        )
        for position, row in enumerate(rows, start=1)
    )


def points_table(
    team_ids: Sequence[str],
    lines: Iterable[TeamMatchLine],
    config: RuleConfig,
) -> tuple[StandingsRow, ...]:
    """Alias for callers that use the competition wording."""

    return build_standings(team_ids, lines, config)

"""Player comparison (Phase F Group 4): validation + metric deltas.

Compare 2–4 players on their digest-validated career aggregates (optionally
scoped to one format). Validation is strict: the count must be 2–4 and every
id must resolve to a player; anything else is a ``ValueError`` the API maps to
``invalid_query``. Deltas are relative to the first player.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from apps.core.enums import StatScope
from apps.players.models import Player
from apps.statistics.models import PlayerCareerStat

_MIN_PLAYERS = 2
_MAX_PLAYERS = 4

#: metric → PlayerCareerStat field.
_METRIC_FIELDS: dict[str, str] = {
    "runs": "runs",
    "batting_avg": "batting_avg",
    "strike_rate": "strike_rate",
    "hundreds": "hundreds",
    "fifties": "fifties",
    "wickets": "wickets",
    "economy": "economy",
    "bowling_avg": "bowling_avg",
}

DEFAULT_METRICS = ("runs", "batting_avg", "strike_rate", "wickets", "economy")

SUPPORTED_METRICS = frozenset(_METRIC_FIELDS)


def _delta(left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    value = right - left
    if isinstance(value, Decimal):
        return float(round(value, 3))
    return value


def compare_players(
    ids: Sequence[str],
    *,
    format_type: str | None = None,
    metric: str | None = None,
) -> list[dict[str, Any]]:
    """Compare 2–4 players; deltas are relative to the first id."""
    unique = list(dict.fromkeys(ids))
    if not (_MIN_PLAYERS <= len(unique) <= _MAX_PLAYERS):
        raise ValueError(f"compare needs between {_MIN_PLAYERS} and {_MAX_PLAYERS} player ids")
    metrics = [metric] if metric else list(DEFAULT_METRICS)
    if metric is not None and metric not in _METRIC_FIELDS:
        raise ValueError(f"unsupported metric: {metric!r}")

    players = {str(p.id): p for p in Player.objects.filter(id__in=unique)}
    missing = [pid for pid in unique if pid not in players]
    if missing:
        raise ValueError(f"unknown player id: {missing[0]}")

    rows: list[dict[str, Any]] = []
    for pid in unique:
        stat = PlayerCareerStat.objects.filter(
            player_id=pid, scope_type=StatScope.CAREER.value, format_type=format_type
        ).first()
        values = {
            m: (getattr(stat, _METRIC_FIELDS[m]) if stat is not None else None) for m in metrics
        }
        rows.append(
            {
                "player_id": pid,
                "player_name": players[pid].full_name,
                "values": values,
            }
        )

    base = rows[0]["values"]
    for row in rows:
        row["delta"] = {m: _delta(base[m], row["values"][m]) for m in metrics}
    return rows


__all__ = ["SUPPORTED_METRICS", "compare_players"]

"""Leaderboard + form-trend population (Phase F Group 4).

Leaderboards are precomputed top-N player rows per (scope, metric, format)
from the digest-validated ``PlayerCareerStat`` aggregates, never from raw
matches (team aggregates are served by the records catalogue, not this table).
``ux_leaderboard`` folds ``rank`` into the unique, so a scope whose inputs
digest changed is deleted and bulk-inserted atomically — rank shifts never
collide with stale rows. The tie-break is deterministic: value (asc/desc per
metric) then player id, so recomputes are stable.

Form trends are rolling per-player per-format windows (``form_trend``):
``series`` carries the chronological per-match values and ``value`` the window
aggregate, recomputed from the per-match source rows. Fielding metrics have no
source rows yet (per-match fielding is not captured), so their leaderboards
are honest empties.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.core import metrics as core_metrics
from apps.core.enums import MatchStatus
from apps.scoring.config import preset_for_format
from apps.scoring.types import BattingLine, BowlingLine, Format
from apps.statistics.models import (
    FormTrend,
    Leaderboard,
    PlayerCareerStat,
    PlayerMatchStat,
    SourceKind,
    TeamStat,
)
from apps.statistics.services.recompute import _dec, _digest, _lines_from, _stat_source

#: Top-N rows per leaderboard scope.
_TOP_N = 50
#: Rolling form windows (matches).
_FORM_WINDOWS = (5, 10)

#: metric → (aggregate field, ascending) — ascending means lower is better.
_PLAYER_BATTING_METRICS: dict[str, tuple[str, bool]] = {
    "runs": ("runs", False),
    "batting_avg": ("batting_avg", False),
    "strike_rate": ("strike_rate", False),
    "high_score": ("high_score", False),
    "hundreds": ("hundreds", False),
    "fifties": ("fifties", False),
    "fours": ("fours", False),
    "sixes": ("sixes", False),
}
_PLAYER_BOWLING_METRICS: dict[str, tuple[str, bool]] = {
    "wickets": ("wickets", False),
    "bowling_avg": ("bowling_avg", True),
    "economy": ("economy", True),
    "bowling_sr": ("bowling_sr", True),
    "five_wicket_hauls": ("five_wicket_hauls", False),
}
_PLAYER_FIELDING_METRICS: dict[str, tuple[str, bool]] = {
    "catches": ("catches", False),
    "stumpings": ("stumpings", False),
}

#: Public metric catalogue for the API layer (leaderboard → supported metrics).
PLAYER_BATTING_METRICS = frozenset(_PLAYER_BATTING_METRICS)
PLAYER_BOWLING_METRICS = frozenset(_PLAYER_BOWLING_METRICS)
PLAYER_FIELDING_METRICS = frozenset(_PLAYER_FIELDING_METRICS)


# --------------------------------------------------------------------------- #
# Leaderboards
# --------------------------------------------------------------------------- #


def _supporting(metric: str, stat: Any) -> dict[str, Any]:
    if metric in _PLAYER_BATTING_METRICS:
        return {
            "matches_counted": stat.matches_counted,
            "innings": stat.innings,
            "not_outs": stat.not_outs,
        }
    if metric in _PLAYER_BOWLING_METRICS:
        return {
            "matches_counted": stat.matches_counted,
            "balls_bowled": stat.balls_bowled,
            "runs_conceded": stat.runs_conceded,
        }
    return {"matches_counted": stat.matches_counted}


def _ranked_rows(
    *,
    scope_type: str,
    scope_ref_id: Any = None,
    fmt: str | None,
    entity_field: str,
    metrics: dict[str, tuple[str, bool]],
) -> tuple[list[Leaderboard], list[dict[str, Any]]]:
    """Build ranked ``Leaderboard`` rows for one scope + format from aggregates."""
    model: Any = PlayerCareerStat if entity_field == "player" else TeamStat
    qs = model.objects.filter(scope_type=scope_type, scope_ref_id=scope_ref_id)
    if fmt:
        qs = qs.filter(format_type=fmt)
    rows: list[Leaderboard] = []
    source: list[dict[str, Any]] = []

    for metric, (field, ascending) in metrics.items():
        candidates = [(s, getattr(s, field)) for s in qs]
        candidates = [(s, v) for s, v in candidates if v is not None and v != 0]

        def sort_key(item: tuple[Any, Any], *, _ascending: bool = ascending) -> tuple[Any, str]:
            _s, value = item
            key: Any = value if _ascending else -value
            return (key, str(getattr(_s, f"{entity_field}_id")))

        candidates.sort(key=sort_key)
        for rank, (stat, value) in enumerate(candidates[:_TOP_N], start=1):
            source.append(
                {
                    "entity": entity_field,
                    "id": str(getattr(stat, f"{entity_field}_id")),
                    "metric": metric,
                    "value": str(value),
                    "rank": rank,
                    "rule_version": stat.rule_version,
                }
            )
            kwargs: dict[str, Any] = {
                "scope_type": scope_type,
                "scope_ref_id": scope_ref_id,
                "metric": metric,
                "format_type": fmt,
                "rank": rank,
                "value": _dec(value, 3),
                "supporting": _supporting(metric, stat),
                "rule_version": stat.rule_version,
                "source_kind": SourceKind.COMPUTED,
                "matches_counted": stat.matches_counted,
                "partial_matches": stat.partial_matches,
                "first_match_date": stat.first_match_date,
                "last_match_date": stat.last_match_date,
                "is_partial": stat.is_partial,
                "computed_at": timezone.now(),
            }
            if entity_field == "player":
                kwargs["player_id"] = stat.player_id
            else:
                kwargs["team_id"] = stat.team_id
            rows.append(Leaderboard(**kwargs))
    return rows, source


def _rebuild_scope(
    *,
    scope_type: str,
    scope_ref_id: Any = None,
    fmt: str | None,
    entity_field: str,
    metrics: dict[str, tuple[str, bool]],
) -> None:
    rows, source = _ranked_rows(
        scope_type=scope_type,
        scope_ref_id=scope_ref_id,
        fmt=fmt,
        entity_field=entity_field,
        metrics=metrics,
    )
    digest = _digest(source)
    existing = Leaderboard.objects.filter(
        scope_type=scope_type, scope_ref_id=scope_ref_id, format_type=fmt
    ).first()
    if existing is not None and existing.inputs_digest == digest:
        return  # untouched: digest unchanged
    Leaderboard.objects.filter(
        scope_type=scope_type, scope_ref_id=scope_ref_id, format_type=fmt
    ).delete()
    if rows:
        for row in rows:
            row.inputs_digest = digest
        Leaderboard.objects.bulk_create(rows)
    core_metrics.LEADERBOARD_REBUILDS.labels(scope=scope_type).inc()


def recompute_player_leaderboard(
    scope_type: str, scope_ref_id: Any = None, fmt: str | None = None
) -> None:
    """Rebuild the player leaderboard for one scope + ref + format (digest-gated).

    ``ux_leaderboard`` does not distinguish player vs team rows, and team
    aggregates are served by the records catalogue (``/statistics/teams``),
    so the leaderboard table holds player rows only.
    """
    metrics = {
        **_PLAYER_BATTING_METRICS,
        **_PLAYER_BOWLING_METRICS,
        **_PLAYER_FIELDING_METRICS,
    }
    _rebuild_scope(
        scope_type=scope_type,
        scope_ref_id=scope_ref_id,
        fmt=fmt,
        entity_field="player",
        metrics=metrics,
    )


# --------------------------------------------------------------------------- #
# Form trends
# --------------------------------------------------------------------------- #


def _match_form_values(row: PlayerMatchStat) -> dict[str, Any]:
    batting = _lines_from(row.batting, BattingLine)
    bowling = _lines_from(row.bowling, BowlingLine)
    return {
        "match_id": str(row.match_id),
        "match_date": str(row.match.source_local_date),
        "runs": sum(b.runs for b in batting),
        "balls_faced": sum(b.balls for b in batting),
        "dismissals": sum(1 for b in batting if b.out),
        "wickets": sum(b.wickets for b in bowling),
        "runs_conceded": sum(b.runs_conceded for b in bowling),
        "balls_bowled": sum(b.legal_balls for b in bowling),
    }


def _window_aggregates(per_match: list[dict[str, Any]], window: int) -> dict[str, Any]:
    recent = per_match[-window:]
    dismissals = sum(m["dismissals"] for m in recent)
    balls_faced = sum(m["balls_faced"] for m in recent)
    balls_bowled = sum(m["balls_bowled"] for m in recent)
    runs = sum(m["runs"] for m in recent)
    runs_conceded = sum(m["runs_conceded"] for m in recent)
    wickets = sum(m["wickets"] for m in recent)
    return {
        "matches": len(recent),
        "runs": runs,
        "batting_avg": (runs / dismissals) if dismissals else None,
        "strike_rate": ((runs / balls_faced) * 100) if balls_faced else None,
        "wickets": wickets,
        "economy": (runs_conceded / (balls_bowled / 6)) if balls_bowled else None,
    }


def recompute_form_key(player_id: Any, fmt: str | None) -> None:
    """Rebuild one player's form windows for a format (digest-gated)."""
    qs = PlayerMatchStat.objects.filter(player_id=player_id, match__status=MatchStatus.COMPLETED)
    if fmt:
        qs = qs.filter(match__format_type=fmt)
    rows = list(qs.select_related("match").order_by("match__source_local_date", "match_id"))
    digest = _digest([_stat_source(r) for r in rows])
    existing = FormTrend.objects.filter(player_id=player_id, format_type=fmt).first()
    if existing is not None and existing.inputs_digest == digest:
        return
    FormTrend.objects.filter(player_id=player_id, format_type=fmt).delete()
    if not rows:
        return

    per_match = [_match_form_values(r) for r in rows]
    config = preset_for_format(Format(rows[0].match.format_type))
    dates = [r.match.source_local_date for r in rows if r.match.source_local_date]
    for window in _FORM_WINDOWS:
        aggregates = _window_aggregates(per_match, window)
        for metric, value in aggregates.items():
            FormTrend.objects.create(
                player_id=player_id,
                format_type=fmt,
                window_size=window,
                metric=metric,
                value=_dec(value, 3) if value is not None else None,
                series=per_match,
                rule_version=config.version,
                inputs_digest=digest,
                source_kind=SourceKind.COMPUTED,
                matches_counted=len({m["match_id"] for m in per_match}),
                partial_matches=sum(1 for r in rows if r.is_partial),
                first_match_date=min(dates) if dates else None,
                last_match_date=max(dates) if dates else None,
                is_partial=any(r.is_partial for r in rows),
                computed_at=timezone.now(),
            )


__all__ = [
    "PLAYER_BATTING_METRICS",
    "PLAYER_BOWLING_METRICS",
    "PLAYER_FIELDING_METRICS",
    "recompute_form_key",
    "recompute_player_leaderboard",
]

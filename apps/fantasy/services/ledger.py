"""Append-only points ledger service (Task 4).

The ledger is append-only: ``UPDATE`` and ``DELETE`` are blocked for every role
by the migration 0002 trigger, so "supersession" cannot mutate old rows.
Instead the current state is *derived*:

- every recompute writes a ``fantasy_calc_audit`` row (the run) and one ledger
  row per player with ``calc_run_id`` = that run;
- the *current* rows for a (match, ruleset version) are exactly the rows whose
  ``calc_run_id`` is the latest (non-provisional) audit row for that pair;
- ``sum_current`` is the sum of those rows (one row per player in practice).

``is_current``/``superseded_by`` remain the DDL's informational columns —
every appended row keeps ``is_current=True`` and rows are never mutated.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Sum
from django.db.models.query import QuerySet

from apps.fantasy import metrics
from apps.fantasy.models import FantasyCalcAudit, FantasyPointsLedger

#: Ledger rows written for an engine result carry this event_type so the
#: append-only history is self-describing (vs. manual adjustments, Task 5).
EVENT_TYPE_COMPUTED = "computed"


def latest_calc_run(
    match_id: Any,
    ruleset_version_id: Any,
    *,
    provisional: bool = False,
) -> FantasyCalcAudit | None:
    """The most recent calc run for (match, version), or None."""
    return (
        FantasyCalcAudit.objects.filter(
            match_id=match_id,
            ruleset_version_id=ruleset_version_id,
            is_provisional=provisional,
        )
        .order_by("-computed_at", "-id")
        .first()
    )


def append_run(audit: FantasyCalcAudit, result: Any) -> list[FantasyPointsLedger]:
    """Append one ledger row per player for a calc run.

    Rows carry ``calc_run_id`` and the run's ``computed_at`` so the history is
    reproducible and the current state derives unambiguously.
    """
    rows = [
        FantasyPointsLedger(
            match_id=audit.match_id,
            player_id=UUID(player.player_id),
            ruleset_version_id=audit.ruleset_version_id,
            delta=player.total,
            event_type=EVENT_TYPE_COMPUTED,
            reason=f"calc run {audit.id} ({audit.trigger or 'unknown'})",
            created_at=audit.computed_at,
            calc_run_id=audit.id,
        )
        for player in result.players
    ]
    created = FantasyPointsLedger.objects.bulk_create(rows)
    metrics.FANTASY_LEDGER_GROWTH.inc(len(created))
    return created


def current_rows(
    match_id: Any,
    ruleset_version_id: Any,
    *,
    provisional: bool = False,
) -> QuerySet[FantasyPointsLedger]:
    """The current ledger rows for (match, version): the latest run's rows."""
    audit = latest_calc_run(match_id, ruleset_version_id, provisional=provisional)
    if audit is None:
        return FantasyPointsLedger.objects.none()
    return FantasyPointsLedger.objects.filter(
        match_id=match_id,
        ruleset_version_id=ruleset_version_id,
        calc_run_id=audit.id,
    )


def current_totals(
    match_id: Any,
    ruleset_version_id: Any,
    *,
    provisional: bool = False,
) -> dict[str, Decimal]:
    """Current per-player totals (player_id str -> delta)."""
    return {
        str(row.player_id): row.delta
        for row in current_rows(match_id, ruleset_version_id, provisional=provisional)
    }


def sum_current(
    match_id: Any,
    player_id: Any,
    ruleset_version_id: Any,
    *,
    provisional: bool = False,
) -> Decimal:
    """A player's current points for (match, version) (0.00 when none)."""
    total = (
        current_rows(match_id, ruleset_version_id, provisional=provisional)
        .filter(player_id=player_id)
        .aggregate(total=Sum("delta"))["total"]
    )
    return total if total is not None else Decimal("0.00")


__all__ = [
    "EVENT_TYPE_COMPUTED",
    "append_run",
    "current_rows",
    "current_totals",
    "latest_calc_run",
    "sum_current",
]

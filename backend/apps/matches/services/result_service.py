"""Result finalisation (ROADMAP Phase D task 5).

Completion is derived **only** from stored deliveries by the pure engine's
``results`` module (called through ``scorecards.rebuild_match``). Provider
scores are never written into derived data; a licensed revised target (DLS) is
passed through ``innings.dls_target`` and the engine raises
``LicensedMethodRequired`` if any code tries to compute it.

Completion happens once: the per-match advisory lock + an existence check for
``match.completed`` in the event log make duplicate/concurrent finalisations
idempotent, so the Tier-2 statistics rebuild triggered by that event runs once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction

from apps.core.enums import MatchStatus
from apps.matches import event_schemas
from apps.matches.models import Match, MatchEventLog, MatchResult
from apps.matches.services import event_log
from apps.scorecards.services.rebuild import rebuild_match


@dataclass(frozen=True)
class ResultReport:
    """The outcome of a finalisation attempt."""

    match_id: str
    status: str
    result_type: str | None
    winner_team_id: str | None
    margin_type: str | None
    margin_value: int | None
    result_text: str | None
    is_dls: bool
    is_super_over: bool
    completed_now: bool
    result_changed: bool
    already_complete: bool
    digest: str


def _current_result(match: Match) -> MatchResult | None:
    return MatchResult.objects.filter(match_id=match.id).first()


def _result_report(
    match: Match,
    *,
    completed_now: bool,
    result_changed: bool,
    already_complete: bool,
    digest: str,
) -> ResultReport:
    row = _current_result(match)
    return ResultReport(
        match_id=str(match.id),
        status=match.status,
        result_type=row.result_type if row else match.result_type,
        winner_team_id=str(row.winner_team_id) if row and row.winner_team_id else None,
        margin_type=row.margin_type if row else None,
        margin_value=row.margin_value if row else None,
        result_text=row.result_text if row else match.result_text,
        is_dls=bool(row.is_dls) if row else False,
        is_super_over=bool(row.is_super_over) if row else False,
        completed_now=completed_now,
        result_changed=result_changed,
        already_complete=already_complete,
        digest=digest,
    )


def _completed_payload(match: Match) -> dict[str, Any]:
    row = _current_result(match)
    return {
        "result_type": row.result_type if row else (match.result_type or "NO_RESULT"),
        "winner_team_id": str(row.winner_team_id) if row and row.winner_team_id else None,
        "margin_type": row.margin_type if row else None,
        "margin_value": row.margin_value if row else None,
        "result_text": row.result_text if row else match.result_text,
        "is_dls": bool(row.is_dls) if row else False,
        "is_super_over": bool(row.is_super_over) if row else False,
        "digest": str(match.revision),
    }


def finalise(match: Match, *, source: Any = None, force: bool = False) -> ResultReport:
    """Finalise a match once; return the current result.

    ``force=True`` re-derives an already-completed match (used after a
    provider correction). Without ``force`` a completed match is a no-op, so
    ``match.completed`` is published exactly once.
    """
    with transaction.atomic():
        event_log.lock_match(match.id)
        match.refresh_from_db()
        existing = _current_result(match)
        already = (
            match.status == MatchStatus.COMPLETED
            and match.result_type is not None
            and existing is not None
        )
        if already and not force:
            return _result_report(
                match,
                completed_now=False,
                result_changed=False,
                already_complete=True,
                digest=str(match.revision),
            )

        previous_status = match.status
        previous_result = (
            (
                existing.result_type,
                str(existing.winner_team_id) if existing.winner_team_id else None,
            )
            if existing
            else None
        )
        rebuild = rebuild_match(match, source=source)
        match.refresh_from_db()
        completed_now = (
            match.status == MatchStatus.COMPLETED and previous_status != MatchStatus.COMPLETED
        )

        after_row = _current_result(match)
        result_changed = False
        if previous_result is not None and after_row is not None:
            after = (
                after_row.result_type,
                str(after_row.winner_team_id) if after_row.winner_team_id else None,
            )
            result_changed = previous_result != after

        if completed_now:
            event_log.append_event(
                match.id,
                event_schemas.MATCH_STATE_CHANGED,
                {
                    "from": previous_status,
                    "to": match.status,
                    "state_title": match.state_title,
                    "status_detail": match.status_detail,
                    "reopened": False,
                    "source": getattr(source, "code", None),
                },
            )
        if (
            match.status == MatchStatus.COMPLETED
            and not MatchEventLog.objects.filter(
                match_id=match.id, event_type=event_schemas.MATCH_COMPLETED
            ).exists()
        ):
            event_log.append_event(
                match.id,
                event_schemas.MATCH_COMPLETED,
                _completed_payload(match),
            )
        if result_changed and not completed_now:
            event_log.append_event(
                match.id,
                event_schemas.CORRECTION_APPLIED,
                {
                    "entity_type": "match",
                    "entity_id": str(match.id),
                    "field_path": "result",
                    "reason": "result re-evaluated from corrected deliveries",
                    "target_revision": match.revision,
                    "result_changed": True,
                },
            )

        return _result_report(
            match,
            completed_now=completed_now,
            result_changed=result_changed,
            already_complete=already,
            digest=rebuild.digest,
        )


def reevaluate(match: Match, *, source: Any = None) -> ResultReport:
    """Re-derive an already-completed match after a correction."""
    return finalise(match, source=source, force=True)


__all__ = ["ResultReport", "finalise", "reevaluate"]

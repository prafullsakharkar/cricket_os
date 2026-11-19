"""Match lifecycle state machine (DATABASE_SCHEMA §3.1/§3.5, ROADMAP Phase D).

``match.status`` is the **lifecycle only**; the outcome is carried by
``result_type``/``match_result`` (schema §3.1). This module is the single place
that decides whether a lifecycle change is legal:

* :data:`ALLOWED_TRANSITIONS` is the explicit ``from -> {to}`` table;
* :func:`transition` enforces it, raises :class:`IllegalTransition` for a
  forbidden change, and is a no-op when re-applying the current state;
* terminal states never move backwards — except through the explicit,
  audited :func:`reopen` path used for provider corrections.

The function writes the new lifecycle columns and returns ``True`` when a
change happened (``False`` for an idempotent re-apply). Event-log rows are
appended by the caller (``apps.live.services.apply`` / the fixture and
correction services) so sequencing stays inside one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.enums import MatchStatus
from apps.core.errors import CricketError
from apps.matches.models import Match

#: Terminal lifecycle states (no automatic outgoing transition).
TERMINAL: frozenset[str] = frozenset(
    {
        MatchStatus.COMPLETED,
        MatchStatus.ABANDONED,
        MatchStatus.CANCELLED,
        MatchStatus.FORFEITED,
    }
)

#: Explicit transition table. Re-applying the current state is always allowed
#: (idempotent no-op) and is handled before this table is consulted.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    MatchStatus.SCHEDULED: frozenset(
        {
            MatchStatus.TOSS,
            MatchStatus.IN_PROGRESS,
            MatchStatus.POSTPONED,
            MatchStatus.CANCELLED,
            MatchStatus.ABANDONED,
            MatchStatus.FORFEITED,
        }
    ),
    MatchStatus.TOSS: frozenset(
        {
            MatchStatus.IN_PROGRESS,
            MatchStatus.RAIN_DELAY,
            MatchStatus.POSTPONED,
            MatchStatus.CANCELLED,
            MatchStatus.ABANDONED,
            MatchStatus.FORFEITED,
        }
    ),
    MatchStatus.IN_PROGRESS: frozenset(
        {
            MatchStatus.INNINGS_BREAK,
            MatchStatus.DRINKS,
            MatchStatus.RAIN_DELAY,
            MatchStatus.BAD_LIGHT,
            MatchStatus.STUMPS,
            MatchStatus.DAY_BREAK,
            MatchStatus.COMPLETED,
            MatchStatus.ABANDONED,
            MatchStatus.CANCELLED,
            MatchStatus.FORFEITED,
            MatchStatus.POSTPONED,
        }
    ),
    MatchStatus.INNINGS_BREAK: frozenset(
        {
            MatchStatus.IN_PROGRESS,
            MatchStatus.RAIN_DELAY,
            MatchStatus.STUMPS,
            MatchStatus.DAY_BREAK,
            MatchStatus.COMPLETED,
            MatchStatus.ABANDONED,
            MatchStatus.CANCELLED,
            MatchStatus.FORFEITED,
        }
    ),
    MatchStatus.DRINKS: frozenset(
        {
            MatchStatus.IN_PROGRESS,
            MatchStatus.RAIN_DELAY,
            MatchStatus.BAD_LIGHT,
            MatchStatus.STUMPS,
            MatchStatus.COMPLETED,
            MatchStatus.ABANDONED,
        }
    ),
    MatchStatus.RAIN_DELAY: frozenset(
        {
            MatchStatus.IN_PROGRESS,
            MatchStatus.INNINGS_BREAK,
            MatchStatus.STUMPS,
            MatchStatus.DAY_BREAK,
            MatchStatus.COMPLETED,
            MatchStatus.ABANDONED,
            MatchStatus.CANCELLED,
            MatchStatus.FORFEITED,
        }
    ),
    MatchStatus.BAD_LIGHT: frozenset(
        {
            MatchStatus.IN_PROGRESS,
            MatchStatus.STUMPS,
            MatchStatus.DAY_BREAK,
            MatchStatus.COMPLETED,
            MatchStatus.ABANDONED,
        }
    ),
    MatchStatus.STUMPS: frozenset(
        {
            MatchStatus.IN_PROGRESS,
            MatchStatus.DAY_BREAK,
            MatchStatus.COMPLETED,
            MatchStatus.ABANDONED,
            MatchStatus.CANCELLED,
        }
    ),
    MatchStatus.DAY_BREAK: frozenset(
        {
            MatchStatus.IN_PROGRESS,
            MatchStatus.STUMPS,
            MatchStatus.COMPLETED,
            MatchStatus.ABANDONED,
        }
    ),
    MatchStatus.POSTPONED: frozenset(
        {
            MatchStatus.SCHEDULED,
            MatchStatus.TOSS,
            MatchStatus.IN_PROGRESS,
            MatchStatus.CANCELLED,
            MatchStatus.ABANDONED,
            MatchStatus.FORFEITED,
        }
    ),
    # Terminal states have no automatic outgoing edges; ``reopen`` is the
    # explicit, audited exception.
    MatchStatus.COMPLETED: frozenset(),
    MatchStatus.ABANDONED: frozenset(),
    MatchStatus.CANCELLED: frozenset(),
    MatchStatus.FORFEITED: frozenset(),
}


class IllegalTransition(CricketError):
    """A lifecycle change the state machine forbids (turned into a DLQ row)."""

    code = "unprocessable"
    status_code = 422


@dataclass(frozen=True)
class TransitionResult:
    """What :func:`transition` did."""

    match: Match
    previous_status: str
    status: str
    changed: bool
    reopened: bool = False
    payload: dict[str, Any] | None = None


def can_transition(from_status: str, to_status: str, *, reopen: bool = False) -> bool:
    """True when ``from_status -> to_status`` is legal.

    Re-applying the current state is always legal (idempotent no-op). Terminal
    states may only move to ``IN_PROGRESS`` through the explicit reopen path.
    """
    if to_status not in MatchStatus.values:
        return False
    if from_status == to_status:
        return True
    if reopen and from_status in TERMINAL and to_status == MatchStatus.IN_PROGRESS:
        return True
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


def _apply_fields(
    match: Match,
    *,
    status: str,
    state_title: str | None,
    status_detail: str | None,
    extra_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "status": status,
        "revision": (match.revision or 0) + 1,
        "ingested_at": timezone.now(),
    }
    if state_title is not None:
        updates["state_title"] = state_title
    if status_detail is not None:
        updates["status_detail"] = status_detail
    if status in TERMINAL:
        updates["end_time_utc"] = timezone.now()
    if extra_fields:
        updates.update(extra_fields)
    return updates


def transition(
    match: Match,
    to_status: str,
    *,
    state_title: str | None = None,
    status_detail: str | None = None,
    extra_fields: dict[str, Any] | None = None,
    reopen: bool = False,
    source: Any = None,
    emit_event: bool = True,
) -> TransitionResult:
    """Move ``match`` to ``to_status`` under the transition table.

    Idempotent: re-applying the current status updates nothing and returns
    ``changed=False``. The caller is responsible for wrapping this in the
    per-match transaction and appending the corresponding ``match.state_changed``
    event-log row (so event sequencing and the lifecycle change commit or roll
    back together).
    """
    match.refresh_from_db()
    previous = match.status
    if not can_transition(previous, to_status, reopen=reopen):
        raise IllegalTransition(
            f"illegal match transition {previous} -> {to_status}",
            details={"match_id": str(match.id), "from": previous, "to": to_status},
        )
    if previous == to_status:
        return TransitionResult(
            match=match, previous_status=previous, status=to_status, changed=False
        )

    updates = _apply_fields(
        match,
        status=to_status,
        state_title=state_title,
        status_detail=status_detail,
        extra_fields=extra_fields,
    )
    reopened = reopen and previous in TERMINAL
    if reopened:
        # A correction reopened the match: the outcome is re-derived by the
        # result service, so the cached result columns are cleared first.
        updates.update(
            {
                "result_type": None,
                "result_text": None,
                "winner_team_id": None,
                "end_time_utc": None,
            }
        )
    with transaction.atomic():
        Match.objects.filter(id=match.id).update(**updates)
        match.refresh_from_db()
        payload = {
            "from": previous,
            "to": to_status,
            "state_title": match.state_title,
            "status_detail": match.status_detail,
            "reopened": reopened,
            "source": getattr(source, "code", None),
        }
        if emit_event:
            from apps.matches import event_schemas
            from apps.matches.services import event_log

            event_log.append_event(match.id, event_schemas.MATCH_STATE_CHANGED, payload)
    match.refresh_from_db()
    payload = {"match_id": str(match.id), **payload}
    if emit_event:
        # Cache invalidation (Phase E) and any other in-process consumer run
        # after commit; the durable event-log row above is the source of truth.
        from apps.core.events import TOPIC_MATCH_STATE_CHANGED, publish

        publish(TOPIC_MATCH_STATE_CHANGED, payload)
    return TransitionResult(
        match=match,
        previous_status=previous,
        status=to_status,
        changed=True,
        reopened=reopened,
        payload=payload,
    )


def reopen(match: Match, *, reason: str, source: Any = None) -> TransitionResult:
    """The only backwards path: an audited terminal -> IN_PROGRESS reopen.

    Used when a licensed provider corrects a completed match. Callers must
    record the reason (``DataCorrection``/admin audit) and re-run the result
    service afterwards.
    """
    result = transition(
        match,
        MatchStatus.IN_PROGRESS,
        status_detail=reason,
        reopen=True,
        source=source,
    )
    return result


__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL",
    "IllegalTransition",
    "TransitionResult",
    "can_transition",
    "reopen",
    "transition",
]

"""Manual point adjustments (Task 5).

Adjustments correct data errors under steward control. They are:

* **Role-gated** — the actor must belong to the ``data_steward`` or ``admin``
  Phase B role Group (or be a superuser).
* **Bounded + validated** — |delta| <= :data:`MAX_ADJUSTMENT_ABS`, quantized to
  2dp with ROUND_HALF_UP, non-zero, and a non-blank ``reason`` + ``reference``
  are mandatory.
* **Append-only + separate** — the entry lives in ``fantasy_point_adjustment``
  (its own append-only table); computed ``fantasy_points_ledger`` rows are
  never touched.
* **Version-independent** — an adjustment applies to the (match, player) pair
  regardless of ruleset version, so every ``player_match_performance`` row for
  that pair reflects it.
* **Distinct in totals** — the read model's ``total_points`` = computed +
  adjustments, with an ``adjustments`` entry in ``breakdown``. Recomputes
  re-derive the computed component and re-apply the (unchanged) adjustment
  sum, so adjustments survive recomputes.
* **Voided by offset, never edited** — ``void_adjustment`` appends an
  offsetting entry (``delta = -adj.delta``, ``reference = adj.id``).
* **Audited** — every apply/void publishes ``fantasy.adjustment``
  (``on_commit=False``, same transaction) for the existing administration
  audit subscriber.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Sum

from apps.core import events as core_events
from apps.fantasy.models import FantasyPointAdjustment, PlayerMatchPerformance

#: Hard bound for any single manual adjustment (a match's points rarely exceed
#: this; the bound is deliberately conservative and documented in FANTASY.md).
MAX_ADJUSTMENT_ABS = Decimal("1000.00")

#: Role Groups allowed to apply/void adjustments (Phase B).
ADJUSTMENT_ROLES = ("data_steward", "admin")

#: Audit action recorded for a void (distinct from the apply action).
VOID_ACTION = "fantasy.adjustment.void"


def _require_adjustment_role(actor: Any) -> Any:
    """Raise PermissionError unless ``actor`` may apply adjustments.

    ``actor`` must be a User-like object (superuser, or a member of the
    ``data_steward`` or ``admin`` role Group).
    """
    if actor is None or not getattr(actor, "groups", None):
        raise PermissionError("an actor (data_steward/admin user) is required")
    if getattr(actor, "is_superuser", False):
        return actor
    if not actor.groups.filter(name__in=ADJUSTMENT_ROLES).exists():
        raise PermissionError(f"adjustments require membership in one of {ADJUSTMENT_ROLES}")
    return actor


def _actor_uuid(actor: Any) -> UUID | None:
    """The actor's UUID when it has one (else None — the label carries identity).

    Mirrors the players.merge precedent: Django's stock User has an integer pk,
    so ``adjusted_by`` stays NULL and the audit ``actor_label`` records the
    username.
    """
    raw = getattr(actor, "uuid", None)
    if raw is not None:
        try:
            return UUID(str(raw))
        except ValueError:
            return None
    return None


def _actor_label(actor: Any) -> str:
    label = getattr(actor, "username", None)
    return str(label) if label else str(actor)


def _normalize_delta(delta: Any) -> Decimal:
    """Quantize to 2dp (ROUND_HALF_UP), reject zero and out-of-bound values."""
    value = Decimal(str(delta)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value == 0:
        raise ValueError("delta must be non-zero")
    if abs(value) > MAX_ADJUSTMENT_ABS:
        raise ValueError(f"|delta| must not exceed {MAX_ADJUSTMENT_ABS}")
    return value


def _require_text(value: Any, name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def adjustment_total(match_id: Any, player_id: Any) -> Decimal:
    """The net adjustment for a (match, player) — 0.00 when none."""
    total = FantasyPointAdjustment.objects.filter(match_id=match_id, player_id=player_id).aggregate(
        total=Sum("delta")
    )["total"]
    return total if total is not None else Decimal("0.00")


def adjustment_totals_by_player(match_id: Any) -> dict[str, Decimal]:
    """Net adjustment per player (player_id str -> Decimal) for one match."""
    rows = (
        FantasyPointAdjustment.objects.filter(match_id=match_id)
        .values("player_id")
        .annotate(total=Sum("delta"))
    )
    return {str(row["player_id"]): row["total"] for row in rows}


def _publish(
    adj: FantasyPointAdjustment,
    *,
    action: str,
    actor: Any,
    voids: Any = None,
) -> None:
    """Publish the audit event in the same transaction as the change."""
    core_events.publish(
        core_events.TOPIC_FANTASY_ADJUSTMENT,
        {
            "action": action,
            "adjustment_id": str(adj.id),
            "match_id": str(adj.match_id),
            "player_id": str(adj.player_id),
            "delta": str(adj.delta),
            "reason": adj.reason,
            "reference": adj.reference,
            "actor_id": str(_actor_uuid(actor)) if _actor_uuid(actor) else None,
            "actor_label": _actor_label(actor),
            "voids": str(voids) if voids else None,
        },
        on_commit=False,
    )


def _apply_to_read_model(match_id: Any, player_id: Any, delta: Decimal) -> None:
    """Fold the delta into every performance row for the (match, player) pair.

    ``total_points`` shifts by ``delta`` and the ``adjustments`` breakdown
    entry is set to the *net* adjustment sum (the row's own history). The
    computed components are untouched.
    """
    new_total = adjustment_total(match_id, player_id)
    for perf in PlayerMatchPerformance.objects.filter(match_id=match_id, player_id=player_id):
        perf.total_points = perf.total_points + delta
        breakdown = dict(perf.breakdown or {})
        breakdown["adjustments"] = str(new_total)
        perf.breakdown = breakdown
        perf.save(update_fields=["total_points", "breakdown"])


@transaction.atomic
def apply_adjustment(
    *,
    match_id: Any,
    player_id: Any,
    delta: Any,
    reason: str,
    reference: str,
    actor: Any,
) -> FantasyPointAdjustment:
    """Append a manual adjustment (role-gated, bounded, audited)."""
    _require_adjustment_role(actor)
    value = _normalize_delta(delta)
    reason = _require_text(reason, "reason")
    reference = _require_text(reference, "reference")
    adj = FantasyPointAdjustment.objects.create(
        match_id=match_id,
        player_id=player_id,
        delta=value,
        reason=reason,
        reference=reference,
        adjusted_by=_actor_uuid(actor),
    )
    _apply_to_read_model(match_id, player_id, value)
    _publish(adj, action=core_events.TOPIC_FANTASY_ADJUSTMENT, actor=actor)
    return adj


@transaction.atomic
def void_adjustment(adj: FantasyPointAdjustment, *, actor: Any) -> FantasyPointAdjustment:
    """Void an adjustment by appending an offsetting entry (never editing it)."""
    _require_adjustment_role(actor)
    void = FantasyPointAdjustment.objects.create(
        match_id=adj.match_id,
        player_id=adj.player_id,
        delta=-adj.delta,
        reason=f"VOID of {adj.id}: {adj.reason}",
        reference=str(adj.id),
        adjusted_by=_actor_uuid(actor),
    )
    _apply_to_read_model(adj.match_id, adj.player_id, -adj.delta)
    _publish(void, action=VOID_ACTION, actor=actor, voids=adj.id)
    return void


__all__ = [
    "ADJUSTMENT_ROLES",
    "MAX_ADJUSTMENT_ABS",
    "VOID_ACTION",
    "adjustment_total",
    "adjustment_totals_by_player",
    "apply_adjustment",
    "void_adjustment",
]

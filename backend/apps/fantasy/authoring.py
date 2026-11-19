"""Ruleset authoring service (Task 6).

Turns validated engine ``PointRuleSpec`` rules into stored versions. The
lifecycle is **draft -> frozen** (immutable thereafter) with an optional
``set_default`` step *before* freezing:

* :func:`create_ruleset` / :func:`create_version` — draft CRUD. A version is
  validated as a whole *before* any row is written (``transaction.atomic``), so
  an invalid ruleset writes nothing and raises the engine's precise error.
* :func:`validate_version` — rebuild the engine ``RulesetSpec`` from the stored
  rows (reusing the pipeline's ``ruleset_spec_for``) and validate it.
* :func:`dry_run_version` — compute a version against a completed match's
  snapshot and diff it per player against another version. Reads/writes
  nothing.
* :func:`freeze_version` — **irreversible**: records ``content_hash`` (engine
  ``content_hash`` of the spec) and flips ``is_frozen``/``status``. After the
  transition the DB trigger blocks every later write to the version and its
  rules, and the service refuses a second freeze. ``admin`` role only.
* :func:`set_default_version` — at most one default per (ruleset, format)
  (partial unique index ``ux_frv_default`` is the DB backstop). The default
  must be chosen on a **draft** and survives freezing: a frozen version is
  immutable, so ``is_default`` cannot be flipped after the freeze transition.
* :func:`retire_version` — **forks a new draft** (same rules/policies/format,
  next version number) and never mutates the original: immutable versions
  cannot be edited, so retirement is supersession by a fresh editable copy
  (the "freeze mistake -> new version, never edit" runbook).

``admin`` role checks use the Phase B role Groups (superuser passes).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.fantasy.engine import (
    PointRuleSpec,
    RulesetPolicies,
    RulesetSpec,
    TierSpec,
    compute,
    content_hash,
    point_rule,
    validate_ruleset,
)
from apps.fantasy.models import (
    FantasyPointRule,
    FantasyRuleset,
    FantasyRulesetVersion,
    RulesetVersionStatus,
)
from apps.fantasy.services.recompute import ruleset_spec_for
from apps.fantasy.services.snapshot import build_snapshot

#: Role Groups allowed to freeze / set default / retire (Phase B).
AUTHORING_ADMIN_ROLES = ("admin",)

#: Engine policy flag names (stored under ``version.policies`` as a JSON dict).
_POLICY_FIELDS = ("super_over", "abandoned", "reduced_overs", "retirement_hurt", "substitutes")


class AuthoringError(ValueError):
    """A lifecycle transition is invalid (already frozen, default taken, ...)."""


@dataclass(frozen=True)
class DryRunRow:
    player_id: str
    points: Decimal
    against_points: Decimal | None
    diff: Decimal | None


@dataclass(frozen=True)
class DryRunResult:
    """Per-player dry-run outcome for one version (+ optional comparison)."""

    match_id: str
    version_id: str
    against_id: str | None
    rows: tuple[DryRunRow, ...]
    unsupported_rules: tuple[str, ...]
    is_partial: bool

    def row_for(self, player_id: str) -> DryRunRow | None:
        return next((r for r in self.rows if r.player_id == player_id), None)


def _require_admin(actor: Any) -> None:
    """Raise PermissionError unless ``actor`` is admin-group or a superuser."""
    if actor is None or not getattr(actor, "groups", None):
        raise PermissionError("an actor (admin user) is required")
    if getattr(actor, "is_superuser", False):
        return
    if not actor.groups.filter(name__in=AUTHORING_ADMIN_ROLES).exists():
        raise PermissionError(
            f"this operation requires membership in one of {AUTHORING_ADMIN_ROLES}"
        )


def _actor_uuid(actor: Any) -> UUID | None:
    """The actor's UUID when it has one (else None — merge precedent)."""
    raw = getattr(actor, "uuid", None)
    if raw is not None:
        try:
            return UUID(str(raw))
        except ValueError:
            return None
    return None


def _require_text(value: Any, name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise AuthoringError(f"{name} is required")
    return text


# --------------------------------------------------------------------------- #
# Engine spec <-> DB rows
# --------------------------------------------------------------------------- #


def _condition_for(rule: PointRuleSpec) -> dict[str, Any]:
    """The DB ``condition`` JSON for an engine rule (mirror of the factory)."""
    params: dict[str, Any] = {"operator": rule.operator}
    if rule.unit is not None:
        params["unit"] = rule.unit
    if rule.min_value is not None:
        params["min_value"] = rule.min_value
    if rule.max_value is not None:
        params["max_value"] = rule.max_value
    if rule.tiers:
        params["tiers"] = [(t.min_value, str(t.points)) for t in rule.tiers]
    if rule.tier_mode is not None:
        params["tier_mode"] = rule.tier_mode
    if rule.min_balls is not None:
        params["min_balls"] = rule.min_balls
    if rule.min_overs is not None:
        params["min_overs"] = rule.min_overs
    if rule.cap_value is not None:
        params["cap_value"] = str(rule.cap_value)
    return params


def _rule_from_db(rule: FantasyPointRule) -> PointRuleSpec:
    """Reconstruct an engine rule from a stored row (mirror of the pipeline's
    ``_point_rule_from_db``, so a retired fork preserves every parameter)."""
    condition = rule.condition or {}
    return point_rule(
        metric=rule.event_type,
        operator=condition.get("operator") or "",
        points=rule.points,
        scope=rule.role_scope or "",
        unit=condition.get("unit"),
        min_value=condition.get("min_value"),
        max_value=condition.get("max_value"),
        # ``tiers`` is a JSON list of [min_value, points] pairs; the engine
        # factory normalizes it into ``TierSpec`` objects.
        tiers=cast(
            "Sequence[tuple[int, Decimal | int | str] | TierSpec]",
            condition.get("tiers") or (),
        ),
        tier_mode=condition.get("tier_mode"),
        min_balls=condition.get("min_balls"),
        min_overs=condition.get("min_overs"),
        cap_value=condition.get("cap_value"),
    )


def _spec_from_parts(
    *,
    ruleset_id: str,
    version_number: int,
    format_scope: str | None,
    rules: Sequence[PointRuleSpec],
    policies: dict[str, Any] | None,
) -> RulesetSpec:
    """Assemble + validate a ``RulesetSpec`` from raw authoring parts."""
    defaults = RulesetPolicies()
    policy_values = {name: getattr(defaults, name) for name in _POLICY_FIELDS}
    policy_values.update(policies or {})
    spec = RulesetSpec(
        ruleset_id=ruleset_id,
        version=version_number,
        format_scope=format_scope,
        rules=tuple(rules),
        policies=RulesetPolicies(**policy_values),
    )
    validate_ruleset(spec)
    return spec


# --------------------------------------------------------------------------- #
# Draft CRUD
# --------------------------------------------------------------------------- #


def create_ruleset(
    *,
    name: str,
    provider_scope: str | None = None,
    description: str | None = None,
    actor: Any = None,
) -> FantasyRuleset:
    """Create a ruleset (draft authoring; no role gate — the admin surface is)."""
    return FantasyRuleset.objects.create(
        name=_require_text(name, "name"),
        provider_scope=provider_scope,
        description=description,
        created_by=_actor_uuid(actor),
    )


@transaction.atomic
def create_version(
    *,
    ruleset: FantasyRuleset,
    rules: Sequence[PointRuleSpec] = (),
    policies: dict[str, Any] | None = None,
    format_scope: str | None = None,
    effective_from: Any = None,
    version_number: int | None = None,
    auto_recompute: bool | None = None,
    actor: Any = None,
) -> FantasyRulesetVersion:
    """Create one draft version with its point rules.

    The whole version is validated first: an invalid ruleset raises the
    engine's precise error and writes nothing.
    """
    if version_number is None:
        latest = (
            FantasyRulesetVersion.objects.filter(ruleset=ruleset)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        version_number = (latest or 0) + 1
    if effective_from is None:
        effective_from = timezone.now()
    if auto_recompute is None:
        auto_recompute = settings.FANTASY_DEFAULT_AUTO_RECOMPUTE

    # Validate before persisting anything.
    _spec_from_parts(
        ruleset_id=str(ruleset.id),
        version_number=version_number,
        format_scope=format_scope,
        rules=rules,
        policies=policies,
    )

    version = FantasyRulesetVersion.objects.create(
        ruleset=ruleset,
        version=version_number,
        effective_from=effective_from,
        format_scope=format_scope,
        auto_recompute=auto_recompute,
        policies=policies or {},
    )
    FantasyPointRule.objects.bulk_create(
        [
            FantasyPointRule(
                ruleset_version=version,
                event_type=rule.metric,
                role_scope=rule.scope or None,
                points=rule.points,
                condition=_condition_for(rule),
            )
            for rule in rules
        ]
    )
    return version


# --------------------------------------------------------------------------- #
# Validate + dry-run
# --------------------------------------------------------------------------- #


def validate_version(version: FantasyRulesetVersion) -> RulesetSpec:
    """Rebuild the engine spec from the stored rows and validate it.

    Raises the engine's precise ``RulesetValidationError`` subclasses on the
    first fault (unknown metric, bad operator, contradictory rules, ...).
    """
    return ruleset_spec_for(version)


def dry_run_version(
    version: FantasyRulesetVersion,
    match: Any,
    *,
    against: FantasyRulesetVersion | None = None,
) -> DryRunResult:
    """Compute one version against a match and diff it vs another version.

    Pure read: writes no calc audit, ledger or read-model rows.
    """
    snapshot = build_snapshot(match)
    result = compute(ruleset_spec_for(version), snapshot)
    points = {p.player_id: p.total for p in result.players}

    against_points: dict[str, Decimal] | None = None
    against_id: str | None = None
    if against is not None:
        against_id = str(against.id)
        against_points = {
            p.player_id: p.total for p in compute(ruleset_spec_for(against), snapshot).players
        }

    rows: list[DryRunRow] = []
    for player_id in sorted(set(points) | set(against_points or ())):
        mine = points.get(player_id, Decimal("0.00"))
        other = against_points.get(player_id) if against_points is not None else None
        rows.append(
            DryRunRow(
                player_id=player_id,
                points=mine,
                against_points=other,
                diff=(mine - other) if other is not None else None,
            )
        )
    return DryRunResult(
        match_id=str(match.id),
        version_id=str(version.id),
        against_id=against_id,
        rows=tuple(rows),
        unsupported_rules=result.unsupported_rules,
        is_partial=result.is_partial,
    )


# --------------------------------------------------------------------------- #
# Lifecycle transitions
# --------------------------------------------------------------------------- #


@transaction.atomic
def freeze_version(version: FantasyRulesetVersion, *, actor: Any) -> FantasyRulesetVersion:
    """Freeze a draft version: irreversible, records content_hash, locks it.

    ``admin`` role only. The DB trigger (``OLD.is_frozen``) allows exactly this
    draft->frozen transition and blocks every later write to the version and
    its rules.
    """
    _require_admin(actor)
    if version.is_frozen or version.status == RulesetVersionStatus.FROZEN:
        raise AuthoringError(f"version {version.id} is already frozen")
    if version.status == RulesetVersionStatus.RETIRED:
        raise AuthoringError(f"version {version.id} is retired and cannot be frozen")
    spec = ruleset_spec_for(version)  # cannot freeze an invalid version
    version.is_frozen = True
    version.status = RulesetVersionStatus.FROZEN
    version.content_hash = content_hash(spec)
    version.frozen_at = timezone.now()
    version.frozen_by = _actor_uuid(actor)
    version.save(update_fields=["is_frozen", "status", "content_hash", "frozen_at", "frozen_by"])
    return version


@transaction.atomic
def set_default_version(version: FantasyRulesetVersion, *, actor: Any) -> FantasyRulesetVersion:
    """Mark a draft version as the default for (ruleset, format).

    At most one default per (ruleset, format) — the partial unique index
    ``ux_frv_default`` is the DB backstop. Because frozen versions are
    immutable, the default must be chosen before freezing.
    """
    _require_admin(actor)
    if version.is_frozen or version.status == RulesetVersionStatus.FROZEN:
        raise AuthoringError("a frozen version is immutable: set the default before freezing")
    if version.status == RulesetVersionStatus.RETIRED:
        raise AuthoringError(f"version {version.id} is retired")
    # The partial unique index coalesces a NULL format_scope to 'OTHER', so the
    # at-most-one scope is the version's format (OTHER for any non-format).
    if version.format_scope:
        existing = FantasyRulesetVersion.objects.filter(
            ruleset_id=version.ruleset_id,
            format_scope=version.format_scope,
            is_default=True,
        ).exclude(id=version.id)
    else:
        existing = FantasyRulesetVersion.objects.filter(
            ruleset_id=version.ruleset_id,
            format_scope__isnull=True,
            is_default=True,
        ).exclude(id=version.id)
    if existing.exists():
        raise AuthoringError(
            f"(ruleset {version.ruleset_id}, format "
            f"{version.format_scope or 'OTHER'!r}) already has a default version"
        )
    version.is_default = True
    version.save(update_fields=["is_default"])
    return version


@transaction.atomic
def retire_version(version: FantasyRulesetVersion, *, actor: Any) -> FantasyRulesetVersion:
    """Retire a version by forking a fresh draft (never mutating the original).

    Frozen versions are immutable, so retirement is supersession: the new draft
    copies the rules + policies + format scope with the next version number and
    becomes the editable continuation of the ruleset.
    """
    _require_admin(actor)
    rules = [_rule_from_db(rule) for rule in version.point_rules.order_by("id")]
    return create_version(
        ruleset=version.ruleset,
        rules=tuple(rules),
        policies=version.policies,
        format_scope=version.format_scope,
        actor=actor,
    )


__all__ = [
    "AUTHORING_ADMIN_ROLES",
    "AuthoringError",
    "DryRunResult",
    "DryRunRow",
    "create_ruleset",
    "create_version",
    "dry_run_version",
    "freeze_version",
    "retire_version",
    "set_default_version",
    "validate_version",
]

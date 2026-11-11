"""Fantasy models (DATABASE_SCHEMA §3.8 + Phase H extensions, plan decision 4).

Seven tables: ``fantasy_ruleset``, ``fantasy_ruleset_version``,
``fantasy_point_rule``, ``player_match_performance``, ``fantasy_points_ledger``,
``fantasy_point_adjustment`` and ``fantasy_calc_audit``.

Immutability model:

- **Frozen ruleset versions and their point rules are immutable** at the
  database level (migration 0002's trigger blocks UPDATE/DELETE/INSERT once
  ``is_frozen``) and at the service level (authoring, Task 6). Changing rules
  means a new version.
- **The ledger, adjustments and calc audits are append-only**: UPDATE and
  DELETE are blocked by database triggers for every role. Supersession is
  modelled with ``is_current`` / ``superseded_by``, never mutation. Current
  points = sum of the current ledger entries for (match, player, version).
- ``player_match_performance`` is a *derived read model* (like the Phase F
  aggregates): it is refreshed in place with a bumped ``revision``; the ledger
  is the append-only record of how the totals were built.

The ``condition_hash`` generated column is reproduced as a Django
``GeneratedField`` (see ``expressions.py``) so the value always equals
``md5(condition::text)`` regardless of what the app writes.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.enums import FormatType
from apps.core.models import UUIDPrimaryKeyModel
from apps.fantasy.expressions import ConditionHashExpression


class RulesetVersionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    FROZEN = "frozen", "Frozen"
    RETIRED = "retired", "Retired"


class FantasyRuleset(UUIDPrimaryKeyModel):
    """A named collection of fantasy rule versions (config: SRC)."""

    name = models.TextField()
    provider_scope = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "fantasy_ruleset"

    def __str__(self) -> str:
        return self.name


class FantasyRulesetVersion(UUIDPrimaryKeyModel):
    """One version of a ruleset; frozen versions are immutable audit anchors."""

    ruleset = models.ForeignKey(
        "FantasyRuleset",
        on_delete=models.PROTECT,
        related_name="versions",
        db_index=False,
    )
    version = models.IntegerField()
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    format_scope = models.TextField(choices=FormatType.choices, null=True, blank=True)
    #: Lifecycle flag from the doc DDL; ``status`` is the Phase H lifecycle
    #: column (draft|frozen|retired). Maintained together by the authoring
    #: service: ``is_frozen == (status == 'frozen')``.
    is_frozen = models.BooleanField(default=False)
    status = models.TextField(
        choices=RulesetVersionStatus.choices,
        default=RulesetVersionStatus.DRAFT,
    )
    #: Canonical digest of every rule + parameter (freeze-time, Task 2/6).
    content_hash = models.TextField(null=True, blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)
    frozen_by = models.UUIDField(null=True, blank=True)
    #: At most one default frozen version per (ruleset, format) — enforced by
    #: the partial unique index ``ux_frv_default`` (migration 0001).
    is_default = models.BooleanField(default=False)
    #: Whether the pipeline recomputes this version's points automatically for
    #: terminal matches (set by authoring from FANTASY_DEFAULT_AUTO_RECOMPUTE).
    auto_recompute = models.BooleanField(default=False)

    class Meta:
        db_table = "fantasy_ruleset_version"
        constraints = [
            models.UniqueConstraint(fields=["ruleset_id", "version"], name="ux_frv"),
        ]

    def __str__(self) -> str:
        return f"{self.ruleset_id} v{self.version}"


class FantasyPointRule(UUIDPrimaryKeyModel):
    """One declarative rule in a version (event_type + scope + points + params)."""

    ruleset_version = models.ForeignKey(
        "FantasyRulesetVersion",
        on_delete=models.PROTECT,
        related_name="point_rules",
        db_index=False,
    )
    event_type = models.TextField()
    role_scope = models.TextField(null=True, blank=True)
    points = models.DecimalField(max_digits=6, decimal_places=2)
    condition = models.JSONField(null=True, blank=True)
    #: Generated: md5(coalesce(condition::text, '')) — see expressions.py.
    condition_hash = models.GeneratedField(
        expression=ConditionHashExpression(),
        output_field=models.TextField(),
        db_persist=True,
    )
    cap_kind = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "fantasy_point_rule"
        constraints = [
            models.CheckConstraint(
                condition=Q(cap_kind__isnull=True)
                | Q(cap_kind__in=["none", "per_match", "per_innings"]),
                name="ck_fpr_cap_kind_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}/{self.role_scope}@{self.points}"


class FantasyCalcAudit(UUIDPrimaryKeyModel):
    """Append-only record of one calc run (input/output digests, snapshot)."""

    # Plain uuid cross-references per the §3.8 DDL: the append-only audit and
    # ledger tables must not be FK-bound to mutable entities (e.g. players that
    # merge), so the references are recorded as values, not constraints.
    match_id = models.UUIDField()
    ruleset_version_id = models.UUIDField()
    #: Digest of the input stat snapshot (== snapshot.digest).
    input_digest = models.TextField()
    #: Digest of the computed fantasy result.
    output_digest = models.TextField()
    engine_version = models.TextField()
    notes = models.TextField(null=True, blank=True)
    computed_at = models.DateTimeField(default=timezone.now)
    #: Compressed canonical snapshot JSON (Task 3), so a recomputation is
    #: reproducible even after canonical data later changes.
    snapshot = models.BinaryField(null=True, blank=True)
    snapshot_digest = models.TextField(null=True, blank=True)
    #: What triggered the run: match.completed / correction.applied / backfill /
    #: verify / sweep / merge / provisional.
    trigger = models.TextField(null=True, blank=True)
    is_provisional = models.BooleanField(default=False)
    #: Engine timing breakdown (jsonb), e.g. {"compute_ms": 12, "ledger_ms": 3}.
    timings = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "fantasy_calc_audit"

    def __str__(self) -> str:
        return f"{self.match_id} {self.ruleset_version_id}"


class PlayerMatchPerformance(UUIDPrimaryKeyModel):
    """Derived read model: a player's points for (match, ruleset version)."""

    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="fantasy_performances",
        db_index=False,
    )
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="fantasy_performances",
        db_index=False,
    )
    ruleset_version = models.ForeignKey(
        "FantasyRulesetVersion",
        on_delete=models.PROTECT,
        related_name="performances",
        db_index=False,
    )
    total_points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    #: Per-component breakdown (jsonb): {metric: {points, source}}.
    breakdown = models.JSONField(null=True, blank=True)
    is_playing_xi = models.BooleanField(default=True)
    revision = models.IntegerField(default=1)
    computed_at = models.DateTimeField(default=timezone.now)
    calc_run = models.ForeignKey(
        "FantasyCalcAudit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="performances",
        db_index=False,
    )
    #: Metrics that could not be computed from the snapshot (data availability,
    #: plan decision 8) — never guessed, flagged partial.
    unsupported_rules = models.JSONField(default=list, blank=True)
    is_partial = models.BooleanField(default=False)

    class Meta:
        db_table = "player_match_performance"
        constraints = [
            models.UniqueConstraint(
                fields=["match_id", "player_id", "ruleset_version_id"],
                name="ux_pmp",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} {self.match_id} {self.ruleset_version_id}"


class FantasyPointsLedger(UUIDPrimaryKeyModel):
    """Append-only points ledger: one row per delta, never mutated."""

    # Plain uuid cross-references per the §3.8 DDL (see FantasyCalcAudit).
    match_id = models.UUIDField()
    player_id = models.UUIDField()
    ruleset_version_id = models.UUIDField()
    delta = models.DecimalField(max_digits=8, decimal_places=2)
    event_type = models.TextField(null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.UUIDField(null=True, blank=True)
    #: Append-only supersession: exactly one current row per
    #: (match, player, version) after a recompute; the previous current rows
    #: are flipped to is_current=False and linked to the row that replaced them.
    is_current = models.BooleanField(default=True)
    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_rows",
        db_index=False,
        db_column="superseded_by",
    )

    class Meta:
        db_table = "fantasy_points_ledger"

    def __str__(self) -> str:
        return f"{self.match_id} {self.player_id} {self.delta}"


class FantasyPointAdjustment(UUIDPrimaryKeyModel):
    """Append-only manual adjustment (data errors, steward-approved)."""

    # Plain uuid cross-references per the §3.8 DDL (see FantasyCalcAudit).
    match_id = models.UUIDField()
    player_id = models.UUIDField()
    delta = models.DecimalField(max_digits=8, decimal_places=2)
    reason = models.TextField()
    adjusted_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "fantasy_point_adjustment"

    def __str__(self) -> str:
        return f"{self.match_id} {self.player_id} {self.delta}"

"""Ruleset authoring service (Task 6).

Authoring turns validated ``PointRuleSpec`` rules into stored versions:
create_ruleset / create_version (draft CRUD, next-version numbering, precise
validation errors, nothing written on failure), validate_version (precise
engine errors), dry_run_version (per-player diff vs another version, nothing
written), freeze_version (admin-only, irreversible — records content_hash and
locks the version + its rules at the service and DB level), set_default_version
(admin-only, at most one default per (ruleset, format) — set on a draft,
because a frozen version is immutable), retire_version (admin-only, forks a new
draft, never mutates the original).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import Group, User
from django.db import transaction
from django.db.utils import DatabaseError

from apps.fantasy import authoring
from apps.fantasy.engine import (
    ContradictoryRulesError,
    UnknownMetricError,
    content_hash,
    point_rule,
)
from apps.fantasy.models import (
    FantasyPointRule,
    FantasyRulesetVersion,
    RulesetVersionStatus,
)

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db

RULES_V1 = (point_rule("runs", "per_unit", "1.00", "batting"),)
RULES_V2 = (point_rule("runs", "per_unit", "2.00", "batting"),)


_USER_SEQ = 0


def _make_user(role: str) -> User:
    global _USER_SEQ
    _USER_SEQ += 1
    user = User.objects.create_user(username=f"author-{role}-{_USER_SEQ}", password="pw")
    user.groups.add(Group.objects.get(name=role))
    return user


def _admin() -> User:
    return _make_user("admin")


def _create_version(rules=RULES_V1, **overrides: object):
    ruleset = overrides.pop("ruleset", None) or authoring.create_ruleset(
        name="authoring ruleset", actor=_admin()
    )
    return authoring.create_version(ruleset=ruleset, rules=rules, **overrides)


class TestDraftCrud:
    def test_create_ruleset_and_version_with_rules(self) -> None:
        ruleset = authoring.create_ruleset(
            name="my ruleset", provider_scope="generic", description="d", actor=_admin()
        )
        assert ruleset.name == "my ruleset"
        assert ruleset.is_active is True

        version = authoring.create_version(ruleset=ruleset, rules=RULES_V1, actor=_admin())
        assert version.version == 1
        assert version.status == RulesetVersionStatus.DRAFT
        assert version.is_frozen is False
        assert version.effective_from is not None
        assert version.auto_recompute is True  # settings default
        assert version.point_rules.count() == 1
        rule = version.point_rules.get()
        assert rule.event_type == "runs"
        assert rule.points == Decimal("1.00")

    def test_version_numbering_is_next(self) -> None:
        ruleset = authoring.create_ruleset(name="numbered", actor=_admin())
        v1 = authoring.create_version(ruleset=ruleset, rules=RULES_V1, actor=_admin())
        v2 = authoring.create_version(ruleset=ruleset, rules=RULES_V2, actor=_admin())
        assert v1.version == 1
        assert v2.version == 2

    def test_draft_is_editable(self) -> None:
        version = _create_version()
        rule = version.point_rules.get()
        rule.points = Decimal("3.00")
        rule.save(update_fields=["points"])
        version.refresh_from_db()
        assert version.point_rules.get().points == Decimal("3.00")

    def test_create_version_rejects_invalid_rule_and_writes_nothing(self) -> None:
        ruleset = authoring.create_ruleset(name="invalid", actor=_admin())
        with pytest.raises(UnknownMetricError):
            authoring.create_version(
                ruleset=ruleset,
                rules=(point_rule("not_a_metric", "per_unit", "1.00", "batting"),),
                actor=_admin(),
            )
        assert FantasyRulesetVersion.objects.filter(ruleset=ruleset).count() == 0
        assert FantasyPointRule.objects.count() == 0

    def test_create_version_rejects_contradictory_pair_and_writes_nothing(self) -> None:
        # Each rule is individually valid; together they are contradictory
        # (per_unit and capped both accrue the same runs) — create_version must
        # validate the whole version before writing any row.
        ruleset = authoring.create_ruleset(name="contradictory", actor=_admin())
        with pytest.raises(ContradictoryRulesError):
            authoring.create_version(
                ruleset=ruleset,
                rules=(
                    point_rule("runs", "per_unit", "1.00", "batting"),
                    point_rule("runs", "capped", "2.00", "batting", cap_value="10.00"),
                ),
                actor=_admin(),
            )
        assert FantasyRulesetVersion.objects.filter(ruleset=ruleset).count() == 0
        assert FantasyPointRule.objects.count() == 0


class TestValidate:
    def test_validate_version_accepts_good_ruleset(self) -> None:
        version = _create_version()
        spec = authoring.validate_version(version)
        assert len(spec.rules) == 1

    def test_validate_version_raises_precise_error(self) -> None:
        # Built via the factory (no create_version validation) so the stored
        # rows can hold a contradiction that validate_version must detect.
        _, version = make_ruleset_version(
            rules=(
                point_rule("runs", "per_unit", "1.00", "batting"),
                point_rule("runs", "capped", "2.00", "batting", cap_value="10.00"),
            )
        )
        with pytest.raises(ContradictoryRulesError):
            authoring.validate_version(version)


class TestDryRun:
    def test_dry_run_shows_per_player_diff_and_writes_nothing(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        v1 = _create_version(rules=RULES_V1)
        v2 = _create_version(rules=RULES_V2)

        result = authoring.dry_run_version(v2, match, against=v1)

        assert result.match_id == str(match.id)
        row = result.row_for(str(player.id))
        assert row is not None
        assert row.points == Decimal("116.00")  # 2.00/run
        assert row.against_points == Decimal("58.00")  # 1.00/run
        assert row.diff == Decimal("58.00")
        # Nothing was written (no calc audit, no ledger, no read model).
        from apps.fantasy.models import FantasyCalcAudit, FantasyPointsLedger

        assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 0
        assert FantasyPointsLedger.objects.filter(match_id=match.id).count() == 0

    def test_dry_run_without_against_lists_points_only(self) -> None:
        match, team1, _ = make_completed_match()
        add_stat(match, team1, runs=40, balls=30, out=True)
        version = _create_version()
        result = authoring.dry_run_version(version, match)
        assert result.against_id is None
        assert all(row.against_points is None for row in result.rows)


class TestFreeze:
    def test_freeze_is_irreversible_at_service_and_db_level(self) -> None:
        version = _create_version()
        frozen = authoring.freeze_version(version, actor=_admin())

        assert frozen.is_frozen is True
        assert frozen.status == RulesetVersionStatus.FROZEN
        assert frozen.content_hash == content_hash(authoring.validate_version(version))
        assert frozen.frozen_at is not None
        # Service refuses a second freeze.
        with pytest.raises(authoring.AuthoringError):
            authoring.freeze_version(frozen, actor=_admin())
        # DB refuses any later write (version and its rules) — each in its own
        # savepoint because the trigger RAISE aborts the surrounding txn.
        with pytest.raises(DatabaseError), transaction.atomic():
            FantasyRulesetVersion.objects.filter(id=frozen.id).update(is_frozen=False)
        with pytest.raises(DatabaseError), transaction.atomic():
            FantasyPointRule.objects.filter(ruleset_version_id=frozen.id).update(points="9.00")

    def test_freeze_requires_admin_role(self) -> None:
        version = _create_version()
        for role in ("viewer", "operator", "data_steward"):
            with pytest.raises(PermissionError):
                authoring.freeze_version(version, actor=_make_user(role))
        assert version.is_frozen is False

    def test_freeze_records_frozen_by_when_actor_has_uuid(self) -> None:
        version = _create_version()
        frozen = authoring.freeze_version(version, actor=_admin())
        assert frozen.frozen_by is None  # stock User has no uuid (merge precedent)

    def test_cannot_freeze_invalid_version(self) -> None:
        _, version = make_ruleset_version(
            rules=(
                point_rule("runs", "per_unit", "1.00", "batting"),
                point_rule("runs", "capped", "2.00", "batting", cap_value="10.00"),
            )
        )
        with pytest.raises(ContradictoryRulesError):
            authoring.freeze_version(version, actor=_admin())
        version.refresh_from_db()
        assert version.is_frozen is False


class TestSetDefault:
    def test_set_default_then_freeze_yields_one_frozen_default(self) -> None:
        ruleset = authoring.create_ruleset(name="defaulted", actor=_admin())
        v1 = authoring.create_version(ruleset=ruleset, rules=RULES_V1, actor=_admin())
        authoring.set_default_version(v1, actor=_admin())
        assert v1.is_default is True

        authoring.freeze_version(v1, actor=_admin())
        v1.refresh_from_db()
        assert v1.is_default is True
        assert v1.status == RulesetVersionStatus.FROZEN

    def test_set_default_enforces_at_most_one(self) -> None:
        ruleset = authoring.create_ruleset(name="at-most-one", actor=_admin())
        v1 = authoring.create_version(ruleset=ruleset, rules=RULES_V1, actor=_admin())
        v2 = authoring.create_version(ruleset=ruleset, rules=RULES_V2, actor=_admin())
        authoring.set_default_version(v1, actor=_admin())

        with pytest.raises(authoring.AuthoringError):
            authoring.set_default_version(v2, actor=_admin())
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.is_default is True
        assert v2.is_default is False

    def test_default_scope_is_per_format(self) -> None:
        ruleset = authoring.create_ruleset(name="per-format", actor=_admin())
        t20 = authoring.create_version(
            ruleset=ruleset, rules=RULES_V1, format_scope="T20I", actor=_admin()
        )
        odi = authoring.create_version(
            ruleset=ruleset, rules=RULES_V1, format_scope="ODI", actor=_admin()
        )
        authoring.set_default_version(t20, actor=_admin())
        authoring.set_default_version(odi, actor=_admin())  # different format: fine
        assert FantasyRulesetVersion.objects.filter(is_default=True).count() == 2

    def test_set_default_requires_admin_and_draft(self) -> None:
        version = _create_version()
        for role in ("viewer", "operator", "data_steward"):
            with pytest.raises(PermissionError):
                authoring.set_default_version(version, actor=_make_user(role))
        # Frozen versions are immutable — the default must be set before freezing.
        authoring.freeze_version(version, actor=_admin())
        with pytest.raises(authoring.AuthoringError):
            authoring.set_default_version(version, actor=_admin())


class TestPipelineIntegration:
    def test_authoring_created_version_is_recomputed_by_the_pipeline(self) -> None:
        from apps.fantasy.models import FantasyCalcAudit
        from apps.fantasy.services import recompute

        match, team1, _ = make_completed_match()
        _player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        version = _create_version()  # auto_recompute defaults to the setting

        report = recompute.recompute_match(match)

        assert report.changed is True
        audit = FantasyCalcAudit.objects.get(match_id=match.id, ruleset_version_id=version.id)
        assert audit.version_hash == content_hash(authoring.validate_version(version))

    def test_frozen_authoring_version_content_hash_stable(self) -> None:
        version = _create_version()
        frozen = authoring.freeze_version(version, actor=_admin())
        # The recorded hash matches a fresh recomputation of the spec.
        assert frozen.content_hash == content_hash(authoring.validate_version(frozen))


class TestAdminActions:
    def _request_for(self, user: User) -> object:
        from django.test import RequestFactory

        request = RequestFactory().get("/admin/fantasy/fantasyrulesetversion/")
        request.user = user
        return request

    def test_lifecycle_actions_visible_only_to_admin(self) -> None:
        from django.contrib import admin as dj_admin

        from apps.fantasy.admin import FantasyRulesetVersionAdmin
        from apps.fantasy.models import FantasyRulesetVersion as FRV

        version_admin = FantasyRulesetVersionAdmin(FRV, dj_admin.AdminSite())
        lifecycle = {"freeze_version_action", "set_default_version_action", "retire_version_action"}
        read_only = {"validate_version_action", "dry_run_version_action"}

        for role in ("viewer", "operator", "data_steward"):
            user = _make_user(role)
            visible = set(version_admin.get_actions(self._request_for(user)))
            assert lifecycle.isdisjoint(visible), f"{role} must not see lifecycle actions"
            assert read_only <= visible, f"{role} must see validate/dry-run"

        admin_user = _make_user("admin")
        visible = set(version_admin.get_actions(self._request_for(admin_user)))
        assert lifecycle <= visible


class TestRetire:
    def test_retire_forks_new_version_and_never_mutates(self) -> None:
        ruleset = authoring.create_ruleset(name="retiring", actor=_admin())
        v1 = authoring.create_version(
            ruleset=ruleset, rules=RULES_V1, policies={"super_over": "include"}, actor=_admin()
        )
        v1_snapshot = (v1.version, v1.status, v1.is_frozen, dict(v1.policies or {}))

        v2 = authoring.retire_version(v1, actor=_admin())

        assert v2.id != v1.id
        assert v2.version == 2
        assert v2.status == RulesetVersionStatus.DRAFT
        assert v2.is_frozen is False
        assert v2.ruleset_id == ruleset.id
        assert v2.policies == {"super_over": "include"}
        assert {r.event_type for r in v2.point_rules.all()} == {"runs"}
        # The original is untouched.
        v1.refresh_from_db()
        assert (v1.version, v1.status, v1.is_frozen, dict(v1.policies or {})) == v1_snapshot

    def test_retire_requires_admin_role(self) -> None:
        version = _create_version()
        for role in ("viewer", "operator", "data_steward"):
            with pytest.raises(PermissionError):
                authoring.retire_version(version, actor=_make_user(role))
        assert FantasyRulesetVersion.objects.count() == 1


class TestCoverageBranches:
    """Branches the lifecycle tests don't reach (Task 10 gate)."""

    def test_create_ruleset_requires_nonempty_name(self) -> None:
        with pytest.raises(authoring.AuthoringError):
            authoring.create_ruleset(name="  ")

    def test_freeze_requires_an_actor(self) -> None:
        with pytest.raises(PermissionError):
            authoring.freeze_version(_create_version(), actor=None)

    def test_freeze_allows_superuser(self) -> None:
        superuser = User.objects.create_superuser(username="author-super", password="pw")
        frozen = authoring.freeze_version(_create_version(), actor=superuser)
        assert frozen.is_frozen is True

    def test_actor_uuid_handling_on_create_ruleset(self) -> None:
        import uuid as uuid_mod
        from types import SimpleNamespace

        valid = authoring.create_ruleset(
            name="uuid-ok", actor=SimpleNamespace(uuid=uuid_mod.uuid4())
        )
        assert valid.created_by is not None
        invalid = authoring.create_ruleset(
            name="uuid-bad", actor=SimpleNamespace(uuid="not-a-uuid")
        )
        assert invalid.created_by is None

    def test_create_version_explicit_args_and_full_conditions(self) -> None:
        from datetime import UTC, datetime

        ruleset = authoring.create_ruleset(name="branches", actor=_admin())
        version = authoring.create_version(
            ruleset=ruleset,
            rules=(
                point_rule("runs", "per_unit", "1.00", "batting", unit=2),
                point_rule(
                    "runs", "threshold_bonus", "10.00", "batting", min_value=50, max_value=100
                ),
                point_rule(
                    "runs",
                    "tiered",
                    "1.00",
                    "batting",
                    tiers=[(0, 1), (50, 2)],
                    tier_mode="cumulative",
                ),
                point_rule(
                    "strike_rate_band",
                    "tiered",
                    "1.00",
                    "batting",
                    tiers=[(100, 1)],
                    tier_mode="highest_only",
                    min_balls=10,
                ),
                point_rule(
                    "economy_band",
                    "tiered",
                    "1.00",
                    "bowling",
                    tiers=[(10, 1)],
                    tier_mode="highest_only",
                    min_overs=2,
                ),
                point_rule("wicket", "capped", "20.00", "bowling", cap_value="100.00"),
            ),
            version_number=7,
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            auto_recompute=False,
            actor=_admin(),
        )
        assert version.version == 7
        assert version.auto_recompute is False
        # Two rules share ``runs`` (per_unit + threshold_bonus) — key by
        # (event_type, operator) so neither overwrites the other.
        conditions = {
            (rule.event_type, (rule.condition or {}).get("operator")): rule.condition or {}
            for rule in version.point_rules.all()
        }
        assert conditions[("runs", "per_unit")]["unit"] == 2
        assert conditions[("runs", "threshold_bonus")]["min_value"] == 50
        assert conditions[("runs", "threshold_bonus")]["max_value"] == 100
        assert conditions[("runs", "tiered")]["tiers"] == [[0, "1"], [50, "2"]]
        assert conditions[("runs", "tiered")]["tier_mode"] == "cumulative"
        assert conditions[("strike_rate_band", "tiered")]["min_balls"] == 10
        assert conditions[("economy_band", "tiered")]["min_overs"] == 2
        assert conditions[("wicket", "capped")]["cap_value"] == "100.00"

    def test_freeze_rejects_retired_draft(self) -> None:
        version = _create_version()
        version.status = RulesetVersionStatus.RETIRED
        version.save(update_fields=["status"])
        with pytest.raises(authoring.AuthoringError):
            authoring.freeze_version(version, actor=_admin())

    def test_set_default_rejects_retired_draft(self) -> None:
        version = _create_version()
        version.status = RulesetVersionStatus.RETIRED
        version.save(update_fields=["status"])
        with pytest.raises(authoring.AuthoringError):
            authoring.set_default_version(version, actor=_admin())

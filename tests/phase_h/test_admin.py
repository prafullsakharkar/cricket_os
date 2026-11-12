"""Fantasy admin surface (Task 6/10 gate).

The three append-only tables (ledger, adjustment, calc audit) must expose no
add/delete in the admin, and every authoring + adjustment action must work
through the changelist — confirm page, then the executing POST. Follows the
``tests/admin/test_admin.py`` client convention.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.admin import site
from django.contrib.auth.models import User
from django.test import Client, RequestFactory

from apps.fantasy.models import (
    FantasyCalcAudit,
    FantasyPointAdjustment,
    FantasyPointRule,
    FantasyPointsLedger,
    FantasyRuleset,
    FantasyRulesetVersion,
    PlayerMatchPerformance,
    RulesetVersionStatus,
)

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db

VERSION_CHANGELIST = "/admin/fantasy/fantasyrulesetversion/"
PERF_CHANGELIST = "/admin/fantasy/playermatchperformance/"


@pytest.fixture
def admin_user() -> User:
    return User.objects.create_superuser(username="admin", password="pw")


@pytest.fixture
def client(admin_user: User) -> Client:
    client = Client()
    client.force_login(admin_user)
    return client


def _version() -> FantasyRulesetVersion:
    _, version = make_ruleset_version()
    return version


def _contradictory_version() -> FantasyRulesetVersion:
    """A storable-but-invalid version: per_unit + capped accrue the same runs."""
    version = _version()
    FantasyPointRule.objects.filter(ruleset_version=version).delete()
    FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type="runs",
        role_scope="batting",
        points=Decimal("1.00"),
        condition={"operator": "per_unit"},
    )
    FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type="runs",
        role_scope="batting",
        points=Decimal("2.00"),
        condition={"operator": "capped", "cap_value": "10.00"},
    )
    return version


class TestRegistration:
    def test_every_fantasy_model_is_registered(self) -> None:
        expected = {
            FantasyRuleset,
            FantasyRulesetVersion,
            FantasyPointRule,
            FantasyPointsLedger,
            FantasyPointAdjustment,
            FantasyCalcAudit,
            PlayerMatchPerformance,
        }
        for model in expected:
            assert model in site._registry, f"{model} not registered"

    def test_append_only_admins_forbid_add_and_delete(self, admin_user: User) -> None:
        request = RequestFactory().get("/admin/fantasy/")
        request.user = admin_user
        for model in (FantasyPointsLedger, FantasyPointAdjustment, FantasyCalcAudit):
            admin = site._registry[model]
            assert admin.has_add_permission(request) is False
            assert admin.has_delete_permission(request) is False


class TestVersionActions:
    def test_validate_action_reports_ok(self, client: Client) -> None:
        version = _version()
        response = client.post(
            VERSION_CHANGELIST,
            {
                "action": "validate_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
            },
        )
        assert response.status_code == 302  # back to the changelist

    def test_validate_action_reports_invalid(self, client: Client) -> None:
        version = _contradictory_version()
        response = client.post(
            VERSION_CHANGELIST,
            {
                "action": "validate_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
            },
        )
        assert response.status_code == 302

    def test_dry_run_confirm_then_execute(self, client: Client) -> None:
        match, team1, _ = make_completed_match()
        add_stat(match, team1, runs=58, balls=42, out=True)
        version = _version()

        confirm = client.post(
            VERSION_CHANGELIST,
            {
                "action": "dry_run_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
            },
        )
        assert confirm.status_code == 200

        executed = client.post(
            VERSION_CHANGELIST,
            {
                "action": "dry_run_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
                "match": str(match.id),
            },
        )
        assert executed.status_code == 200
        assert b"Dry-run" in executed.content

    def test_dry_run_missing_match_reports_error(self, client: Client) -> None:
        version = _version()
        response = client.post(
            VERSION_CHANGELIST,
            {
                "action": "dry_run_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302

    def test_dry_run_invalid_version_reports_error(self, client: Client) -> None:
        match, team1, _ = make_completed_match()
        add_stat(match, team1, runs=58, balls=42, out=True)
        version = _contradictory_version()
        response = client.post(
            VERSION_CHANGELIST,
            {
                "action": "dry_run_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
                "match": str(match.id),
            },
        )
        assert response.status_code == 302

    def test_freeze_confirm_then_execute(self, client: Client) -> None:
        version = _version()
        confirm = client.post(
            VERSION_CHANGELIST,
            {
                "action": "freeze_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
            },
        )
        assert confirm.status_code == 200
        assert version.is_frozen is False

        executed = client.post(
            VERSION_CHANGELIST,
            {
                "action": "freeze_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert executed.status_code == 302
        version.refresh_from_db()
        assert version.is_frozen is True
        assert version.status == RulesetVersionStatus.FROZEN
        assert version.content_hash

    def test_set_default_confirm_then_execute(self, client: Client) -> None:
        version = _version()
        confirm = client.post(
            VERSION_CHANGELIST,
            {
                "action": "set_default_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
            },
        )
        assert confirm.status_code == 200

        executed = client.post(
            VERSION_CHANGELIST,
            {
                "action": "set_default_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert executed.status_code == 302
        version.refresh_from_db()
        assert version.is_default is True

    def test_freezing_an_already_frozen_version_reports_error(self, client: Client) -> None:
        version = _version()
        client.post(
            VERSION_CHANGELIST,
            {
                "action": "freeze_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        response = client.post(
            VERSION_CHANGELIST,
            {
                "action": "freeze_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302  # message_user + redirect

    def test_setting_default_on_a_frozen_version_reports_error(self, client: Client) -> None:
        version = _version()
        client.post(
            VERSION_CHANGELIST,
            {
                "action": "freeze_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        response = client.post(
            VERSION_CHANGELIST,
            {
                "action": "set_default_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302

    def test_retiring_an_invalid_version_reports_error(self, client: Client) -> None:
        """Retire forks via create_version, which validates the whole fork —
        a contradictory version cannot be retired."""
        version = _contradictory_version()
        response = client.post(
            VERSION_CHANGELIST,
            {
                "action": "retire_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302
        assert FantasyRulesetVersion.objects.filter(ruleset=version.ruleset).count() == 1

    def test_retire_confirm_then_fork(self, client: Client) -> None:
        version = _version()
        confirm = client.post(
            VERSION_CHANGELIST,
            {
                "action": "retire_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
            },
        )
        assert confirm.status_code == 200

        executed = client.post(
            VERSION_CHANGELIST,
            {
                "action": "retire_version_action",
                "_selected_action": [str(version.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert executed.status_code == 302
        # Retire forks a fresh draft and never mutates the original.
        version.refresh_from_db()
        assert version.status == RulesetVersionStatus.DRAFT
        forked = FantasyRulesetVersion.objects.filter(ruleset=version.ruleset).order_by("-version")
        assert forked.first().version == 2
        assert forked.first().status == RulesetVersionStatus.DRAFT
        assert forked.first().id != version.id


class TestAdjustmentAction:
    def _perf(self, version: FantasyRulesetVersion) -> tuple[object, object, object]:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        from apps.fantasy.services import recompute

        recompute.recompute_match(match)
        perf = PlayerMatchPerformance.objects.get(match_id=match.id, ruleset_version_id=version.id)
        return match, player, perf

    def test_confirm_page_then_apply(self, client: Client) -> None:
        version = _version()
        match, _player, perf = self._perf(version)

        confirm = client.post(
            PERF_CHANGELIST,
            {
                "action": "apply_adjustment_action",
                "_selected_action": [str(perf.pk)],
                "index": "0",
            },
        )
        assert confirm.status_code == 200

        executed = client.post(
            PERF_CHANGELIST,
            {
                "action": "apply_adjustment_action",
                "_selected_action": [str(perf.pk)],
                "index": "0",
                "confirm": "yes",
                "delta": "5.00",
                "reason": "admin test",
                "reference": "REF-ADMIN",
            },
        )
        assert executed.status_code == 302
        adj = FantasyPointAdjustment.objects.get(match_id=match.id)
        assert adj.delta == Decimal("5.00")
        assert adj.reason == "admin test"

    def test_duplicate_match_player_rows_dedupe(self, client: Client) -> None:
        """Two PMP rows for the same (match, player) across versions yield one
        adjustment entry (the confirm page and the apply both dedupe)."""
        version1 = _version()
        match, team1, _ = make_completed_match()
        add_stat(match, team1, runs=58, balls=42, out=True)
        from apps.fantasy.services import recompute

        recompute.recompute_match(match, version_id=version1.id)
        _, version2 = make_ruleset_version(version_number=2)
        recompute.recompute_match(match, version_id=version2.id)
        perfs = list(
            PlayerMatchPerformance.objects.filter(match_id=match.id).order_by("ruleset_version_id")
        )
        assert len(perfs) == 2

        confirm = client.post(
            PERF_CHANGELIST,
            {
                "action": "apply_adjustment_action",
                "_selected_action": [str(p.pk) for p in perfs],
                "index": "0",
            },
        )
        assert confirm.status_code == 200

        executed = client.post(
            PERF_CHANGELIST,
            {
                "action": "apply_adjustment_action",
                "_selected_action": [str(p.pk) for p in perfs],
                "index": "0",
                "confirm": "yes",
                "delta": "2.50",
                "reason": "dedupe",
                "reference": "REF-DEDUPE",
            },
        )
        assert executed.status_code == 302
        assert FantasyPointAdjustment.objects.filter(match_id=match.id).count() == 1

    def test_non_decimal_delta_reports_error(self, client: Client) -> None:
        version = _version()
        _match, _player, perf = self._perf(version)
        response = client.post(
            PERF_CHANGELIST,
            {
                "action": "apply_adjustment_action",
                "_selected_action": [str(perf.pk)],
                "index": "0",
                "confirm": "yes",
                "delta": "abc",
                "reason": "r",
                "reference": "REF",
            },
        )
        # InvalidOperation -> message + re-render of the confirm page.
        assert response.status_code == 200
        assert FantasyPointAdjustment.objects.count() == 0

    def test_invalid_reason_reports_error(self, client: Client) -> None:
        version = _version()
        _match, _player, perf = self._perf(version)
        response = client.post(
            PERF_CHANGELIST,
            {
                "action": "apply_adjustment_action",
                "_selected_action": [str(perf.pk)],
                "index": "0",
                "confirm": "yes",
                "delta": "1.00",
                "reason": "   ",
                "reference": "REF",
            },
        )
        assert response.status_code == 302
        assert FantasyPointAdjustment.objects.count() == 0

    def test_unprivileged_actor_rejected(self) -> None:
        """A crafted POST from a user without the data_steward/admin role is
        rejected by the service (defensive — the action is hidden anyway)."""
        from django.contrib.messages.storage.cookie import CookieStorage
        from django.test import RequestFactory

        from apps.fantasy.admin import apply_adjustment_action

        version = _version()
        _match, _player, perf = self._perf(version)
        viewer = User.objects.create_user(username="adj-viewer", password="pw")
        request = RequestFactory().post(
            PERF_CHANGELIST,
            {"delta": "5.00", "reason": "r", "reference": "REF", "confirm": "yes"},
        )
        request.user = viewer
        setattr(request, "_messages", CookieStorage(request))  # noqa: B010
        modeladmin = site._registry[PlayerMatchPerformance]

        result = apply_adjustment_action(
            modeladmin, request, PlayerMatchPerformance.objects.filter(pk=perf.pk)
        )

        assert result is None
        assert FantasyPointAdjustment.objects.count() == 0

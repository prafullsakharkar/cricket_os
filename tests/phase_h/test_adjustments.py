"""Manual point adjustments (Task 5).

Adjustments correct data errors (steward-approved). They are append-only
(``fantasy_point_adjustment``, never touching computed ledger rows), bounded,
require a reason + reference, apply to a (match, player) pair regardless of
ruleset version, surface as a distinct ``adjustments`` component in the read
model, survive recomputes, and are voided by a new offsetting entry (never
editing the original). Role gate: ``data_steward`` / ``admin`` Groups. Audit:
the existing administration audit subscriber records each apply/void.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import Group, User

from apps.administration.models import AuditLog
from apps.core import events as core_events
from apps.fantasy.models import (
    FantasyPointAdjustment,
    FantasyPointsLedger,
    PlayerMatchPerformance,
)
from apps.fantasy.services import adjustments, recompute

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db


def _make_user(role: str) -> User:
    user = User.objects.create_user(username=f"{role}-{role}", password="pw")
    user.groups.add(Group.objects.get(name=role))
    return user


def _apply(
    match: object,
    player: object,
    *,
    delta: str = "5.00",
    reason: str = "data correction",
    reference: str = "REF-42",
    actor: User,
) -> FantasyPointAdjustment:
    return adjustments.apply_adjustment(
        match_id=match.id,
        player_id=player.id,
        delta=Decimal(delta),
        reason=reason,
        reference=reference,
        actor=actor,
    )


class TestRoleGate:
    def test_viewer_and_operator_denied(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        for role in ("viewer", "operator"):
            user = _make_user(role)
            with pytest.raises(PermissionError):
                _apply(match, player, actor=user)
        assert FantasyPointAdjustment.objects.count() == 0

    def test_data_steward_and_admin_allowed(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        for role in ("data_steward", "admin"):
            user = _make_user(role)
            adj = _apply(match, player, delta="2.50", reference=f"REF-{role}", actor=user)
            # Django's stock User has an integer pk, so the UUID actor column is
            # NULL and the audit label carries the identity (players.merge precedent).
            assert adj.adjusted_by is None
            audit = AuditLog.objects.get(entity_id=adj.id)
            assert audit.actor_label == user.username
            assert audit.actor_id is None
        assert FantasyPointAdjustment.objects.count() == 2

    def test_no_actor_denied(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        with pytest.raises(PermissionError):
            _apply(match, player, actor=None)  # type: ignore[arg-type]


class TestActorIdentity:
    """Actor-identity branches: superuser short-circuit and the UUID column."""

    def test_superuser_user_can_apply(self) -> None:
        """A superuser is allowed regardless of Group membership (branch: the
        is_superuser early return in _require_adjustment_role)."""
        from django.contrib.auth.models import User as AuthUser

        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        superuser = AuthUser.objects.create_superuser(username="adj-super", password="pw")

        adj = _apply(match, player, actor=superuser)

        assert adj.delta == Decimal("5.00")
        # Stock User has no uuid -> the UUID column stays NULL, label carries identity.
        assert adj.adjusted_by is None
        audit = AuditLog.objects.get(entity_id=adj.id)
        assert audit.actor_label == "adj-super"

    def test_actor_with_valid_uuid_records_adjusted_by(self) -> None:
        """An actor exposing a valid ``uuid`` populates ``adjusted_by`` (the
        non-None branch of _actor_uuid)."""
        import uuid as uuid_mod
        from types import SimpleNamespace

        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        actor = SimpleNamespace(
            is_superuser=True,
            uuid=uuid_mod.uuid4(),
            username="uuid-actor",
            groups=object(),
        )

        adj = _apply(match, player, actor=actor)  # type: ignore[arg-type]

        assert adj.adjusted_by == actor.uuid
        audit = AuditLog.objects.get(entity_id=adj.id)
        assert audit.actor_label == "uuid-actor"

    def test_actor_with_invalid_uuid_falls_back_to_none(self) -> None:
        """A malformed uuid is caught (ValueError branch of _actor_uuid) and the
        column stays NULL."""
        from types import SimpleNamespace

        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        actor = SimpleNamespace(
            is_superuser=True,
            uuid="not-a-uuid",
            username="bad-uuid",
            groups=object(),
        )

        adj = _apply(match, player, actor=actor)  # type: ignore[arg-type]

        assert adj.adjusted_by is None
        audit = AuditLog.objects.get(entity_id=adj.id)
        assert audit.actor_label == "bad-uuid"


class TestValidation:
    def test_delta_is_bounded(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        user = _make_user("admin")
        with pytest.raises(ValueError):
            adjustments.apply_adjustment(
                match_id=match.id,
                player_id=player.id,
                delta=adjustments.MAX_ADJUSTMENT_ABS + Decimal("0.01"),
                reason="r",
                reference="REF",
                actor=user,
            )
        # At the bound is allowed (positive and negative).
        assert (
            adjustments.apply_adjustment(
                match_id=match.id,
                player_id=player.id,
                delta=adjustments.MAX_ADJUSTMENT_ABS,
                reason="r",
                reference="REF",
                actor=user,
            ).delta
            == adjustments.MAX_ADJUSTMENT_ABS
        )
        assert (
            adjustments.apply_adjustment(
                match_id=match.id,
                player_id=player.id,
                delta=-adjustments.MAX_ADJUSTMENT_ABS,
                reason="r",
                reference="REF",
                actor=user,
            ).delta
            == -adjustments.MAX_ADJUSTMENT_ABS
        )

    def test_zero_delta_rejected(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        user = _make_user("admin")
        with pytest.raises(ValueError):
            _apply(match, player, delta="0.00", actor=user)

    def test_delta_quantized_round_half_up(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        user = _make_user("admin")
        adj = _apply(match, player, delta="5.005", actor=user)
        assert adj.delta == Decimal("5.01")

    def test_reason_and_reference_mandatory(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        user = _make_user("admin")
        with pytest.raises(ValueError):
            _apply(match, player, reason="", actor=user)
        with pytest.raises(ValueError):
            _apply(match, player, reason="   ", actor=user)
        with pytest.raises(ValueError):
            _apply(match, player, reference="", actor=user)
        with pytest.raises(ValueError):
            _apply(match, player, reference=None, actor=user)  # type: ignore[arg-type]
        assert FantasyPointAdjustment.objects.count() == 0


class TestLedgerSemantics:
    def test_adjustment_is_a_separate_append_only_entry(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        recompute.recompute_match(match)
        computed_ledger = list(FantasyPointsLedger.objects.filter(match_id=match.id))
        assert len(computed_ledger) == 1

        user = _make_user("admin")
        adj = _apply(match, player, actor=user)

        # Computed ledger rows are untouched: no new computed row is appended and
        # nothing is edited (append-only). The adjustment lives in its own table.
        assert list(FantasyPointsLedger.objects.filter(match_id=match.id)) == computed_ledger
        row = FantasyPointAdjustment.objects.get(pk=adj.id)
        assert row.delta == Decimal("5.00")
        assert row.reason == "data correction"
        assert row.reference == "REF-42"
        assert row.adjusted_by is None  # int-pk user: identity lives in the audit label

    def test_adjustment_shown_as_distinct_component(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        recompute.recompute_match(match)

        _apply(match, player, delta="5.00", actor=_make_user("admin"))

        perf = PlayerMatchPerformance.objects.get(match_id=match.id, player_id=player.id)
        assert perf.total_points == Decimal("63.00")
        assert perf.breakdown["runs"] == "58.00"
        assert perf.breakdown["adjustments"] == "5.00"

    def test_recompute_preserves_adjustments(self) -> None:
        match, team1, _ = make_completed_match()
        player, stat = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        recompute.recompute_match(match)
        _apply(match, player, delta="5.00", actor=_make_user("admin"))

        stat.batting = [{**stat.batting[0], "runs": 70, "balls": 50}]
        stat.save(update_fields=["batting"])
        report = recompute.recompute_match(match, trigger="correction.applied")

        perf = PlayerMatchPerformance.objects.get(match_id=match.id, player_id=player.id)
        assert perf.total_points == Decimal("75.00")  # 70 computed + 5 adjustment
        assert perf.breakdown["runs"] == "70.00"
        assert perf.breakdown["adjustments"] == "5.00"
        # The report diff is the computed-only diff (58 -> 70).
        assert report.diff[str(player.id)] == Decimal("12.00")

    def test_void_creates_offsetting_entry(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        recompute.recompute_match(match)
        user = _make_user("admin")
        adj = _apply(match, player, delta="5.00", actor=user)
        assert PlayerMatchPerformance.objects.get(
            match_id=match.id, player_id=player.id
        ).total_points == Decimal("63.00")

        void = adjustments.void_adjustment(adj, actor=user)

        assert void.id != adj.id
        assert void.delta == Decimal("-5.00")
        assert void.reference == str(adj.id)
        assert void.match_id == adj.match_id
        assert void.player_id == adj.player_id
        # The original row is untouched.
        original = FantasyPointAdjustment.objects.get(pk=adj.id)
        assert original.delta == Decimal("5.00")
        assert original.reason == "data correction"
        # Net effect: back to the computed total; both rows remain in history.
        assert adjustments.adjustment_total(match.id, player.id) == Decimal("0.00")
        assert FantasyPointAdjustment.objects.count() == 2
        perf = PlayerMatchPerformance.objects.get(match_id=match.id, player_id=player.id)
        assert perf.total_points == Decimal("58.00")

    def test_recompute_preserves_voided_net_zero(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        recompute.recompute_match(match)
        user = _make_user("admin")
        adj = _apply(match, player, delta="5.00", actor=user)
        adjustments.void_adjustment(adj, actor=user)

        recompute.recompute_match(match)

        perf = PlayerMatchPerformance.objects.get(match_id=match.id, player_id=player.id)
        assert perf.total_points == Decimal("58.00")
        assert perf.breakdown["runs"] == "58.00"


class TestAudit:
    def test_audit_trail_via_audit_subscriber(self) -> None:
        match, team1, _ = make_completed_match()
        player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
        make_ruleset_version()
        user = _make_user("admin")

        adj = _apply(match, player, delta="5.00", reference="REF-AUDIT", actor=user)

        log = AuditLog.objects.get(entity_id=adj.id)
        assert log.action == core_events.TOPIC_FANTASY_ADJUSTMENT
        assert log.entity_type == "fantasy_point_adjustment"
        assert log.actor_label == user.username
        assert log.actor_id is None
        assert log.after["match_id"] == str(match.id)
        assert log.after["delta"] == "5.00"
        assert log.after["reason"] == "data correction"
        assert log.after["reference"] == "REF-AUDIT"

        void = adjustments.void_adjustment(adj, actor=user)
        log2 = AuditLog.objects.get(entity_id=void.id)
        assert log2.action == "fantasy.adjustment.void"
        assert log2.after["voids"] == str(adj.id)

    def test_adjustment_invalidates_read_cache(self) -> None:
        from apps.core.cache import invalidation

        namespaces = invalidation.namespaces_for(
            "fantasy.adjustment", {"match_id": "m1", "adjustment_id": "a1"}
        )
        assert "fantasy" in namespaces
        assert "fantasy:match:m1" in namespaces
        assert "fantasy.adjustment" in invalidation.registered_topics()


class TestAdminAction:
    def _request_for(self, user: User) -> object:
        from django.test import RequestFactory

        request = RequestFactory().get("/admin/fantasy/playermatchperformance/")
        request.user = user
        return request

    def test_adjustment_action_visible_only_to_data_steward_and_admin(self) -> None:
        from django.contrib import admin as dj_admin

        from apps.fantasy.admin import PlayerMatchPerformanceAdmin
        from apps.fantasy.models import PlayerMatchPerformance as PMP

        perf_admin = PlayerMatchPerformanceAdmin(PMP, dj_admin.AdminSite())

        for role in ("data_steward", "admin"):
            user = _make_user(role)
            visible = set(perf_admin.get_actions(self._request_for(user)))
            assert "apply_adjustment_action" in visible
        for role in ("viewer", "operator"):
            user = _make_user(role)
            visible = set(perf_admin.get_actions(self._request_for(user)))
            assert "apply_adjustment_action" not in visible

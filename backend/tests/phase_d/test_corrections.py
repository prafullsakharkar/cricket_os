"""Phase D group 4: corrections (provider revision after completion).

Covers DATA_INGESTION §4.5 / TESTING_STRATEGY §7:

* a provider revision on a completed match supersedes the deliveries, writes
  ``DataCorrection`` rows, rebuilds the innings, re-evaluates the result in
  place (match stays COMPLETED, ``match.completed`` fires exactly once),
  appends ``delivery.corrected``/``correction.applied`` events, triggers the
  Tier-2 statistics subscribers and changes the resource ETag;
* a content-only correction (result unchanged) still appends
  ``correction.applied`` and is audited;
* the steward admin actions are gated by the model's change permission
  (permission matrix).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from django.contrib import admin as dj_admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory

from apps.core import events as core_events
from apps.core.enums import MatchStatus
from apps.core.http import compute_etag
from apps.deliveries.services.validation import DeliveryDraft
from apps.matches import event_schemas
from apps.matches.models import Match, MatchEventLog, MatchResult
from apps.matches.selectors.reads import match_shell
from apps.matches.services import result_service
from apps.statistics.models import PlayerMatchStat
from apps.synchronization.models import DataCorrection

from .test_live_apply import _finish_t20, batch, make_context

pytestmark = pytest.mark.django_db


def _register_participants(ctx: dict[str, object]) -> None:
    """MatchPlayer rows so Tier-2 statistics can attribute lines."""
    from apps.matches.models import MatchPlayer

    MatchPlayer.objects.create(
        match=ctx["match"], player=ctx["batter"], team=ctx["team1"], is_playing_xi=True
    )
    MatchPlayer.objects.create(
        match=ctx["match"], player=ctx["non_striker"], team=ctx["team1"], is_playing_xi=True
    )
    MatchPlayer.objects.create(
        match=ctx["match"], player=ctx["bowler"], team=ctx["team2"], is_playing_xi=True
    )


def _wicket(ctx: dict[str, object], seq: int) -> DeliveryDraft:
    """A corrected delivery: bowled, no runs, next revision.

    ``seq``-th distinct batter is dismissed (the engine counts wickets by
    distinct dismissed players), so the caller supplies a pool of batters.
    """
    batter_id = ctx["wicket_batters"][seq - 1].id  # type: ignore[attr-defined,index]
    return replace(
        batch(ctx, [seq], runs=0).drafts[0],
        batter_id=batter_id,
        is_wicket=True,
        dismissal_type="BOWLED",
        dismissed_player_id=batter_id,
        bowler_credited_wicket=True,
        revision=2,
    )


def _add_wicket_batters(ctx: dict[str, object]) -> None:
    """Create 10 distinct batters for a 10-wicket correction."""
    from tests.phase_c.factories import make_player

    ctx["wicket_batters"] = [make_player(f"Wicket Batter {i}") for i in range(10)]


def _shell_etag(match: Match) -> str:
    return compute_etag(json.dumps(match_shell(match), default=str, sort_keys=True))


def _provider():
    from tests.phase_c.factories import make_provider

    return make_provider("replay")


def test_provider_correction_after_completion_is_fully_audited() -> None:
    from apps.live.services.apply import LiveInningsBatch
    from apps.live.services.corrections import apply_delivery_correction

    ctx = make_context()
    _add_wicket_batters(ctx)
    match = ctx["match"]
    _finish_t20(ctx)
    first = result_service.finalise(match)
    assert first.completed_now is True
    assert first.winner_team_id == str(ctx["team2"].id)
    assert (
        MatchEventLog.objects.filter(
            match_id=match.id, event_type=event_schemas.MATCH_COMPLETED
        ).count()
        == 1
    )
    etag_before = _shell_etag(match)
    stats_before = PlayerMatchStat.objects.filter(match_id=match.id).count()

    # Provider revises innings-2 balls 1-10: all bowled for no runs, so team2
    # (6 all out chasing 61) loses — the result must flip to team1.
    report = apply_delivery_correction(
        match,
        [LiveInningsBatch(innings_number=2, drafts=tuple(_wicket(ctx, s) for s in range(1, 11)))],
        reason="provider revised the innings",
        source=_provider(),
    )

    assert report.superseded == 10
    assert report.result_changed is True
    assert report.result.result_type == "WIN"
    assert report.result.winner_team_id == str(ctx["team1"].id)
    assert report.correction_ids

    match.refresh_from_db()
    assert match.status == MatchStatus.COMPLETED  # stays completed, re-derived in place

    result_row = MatchResult.objects.get(match_id=match.id)
    assert result_row.winner_team_id == ctx["team1"].id
    assert result_row.margin_type == "runs"
    # innings 2 = balls 1-10 bowled for 0 + ball 11 (6) = 6 runs all out.
    assert result_row.margin_value == 60 - 6

    # Audit trail: DataCorrection rows for every superseded delivery.
    corrections = DataCorrection.objects.filter(entity_type="delivery")
    assert corrections.count() >= 10
    assert set(report.correction_ids) <= {
        str(pk) for pk in corrections.values_list("id", flat=True)
    }

    # Event log: corrected deliveries + result-correction event, and exactly
    # one match.completed across both re-derivations.
    types = list(
        MatchEventLog.objects.filter(match_id=match.id)
        .order_by("seq")
        .values_list("event_type", flat=True)
    )
    assert event_schemas.DELIVERY_CORRECTED in types
    assert event_schemas.CORRECTION_APPLIED in types
    assert types.count(event_schemas.MATCH_COMPLETED) == 1

    # ETag must change after the correction; Tier-2 stays untouched by the
    # live path itself (stats build on the post-commit event).
    assert _shell_etag(match) != etag_before
    assert stats_before == 0


@pytest.mark.django_db(transaction=True)
def test_correction_triggers_tier2_rebuild() -> None:
    from apps.live.services.apply import LiveInningsBatch
    from apps.live.services.corrections import apply_delivery_correction
    from apps.statistics.services.stats import _on_match_event

    core_events.clear_subscriptions()
    core_events.subscribe(core_events.TOPIC_MATCH_COMPLETED, _on_match_event)
    core_events.subscribe(core_events.TOPIC_CORRECTION_APPLIED, _on_match_event)

    ctx = make_context()
    _register_participants(ctx)
    match = ctx["match"]
    _finish_t20(ctx)
    result_service.finalise(match)
    assert PlayerMatchStat.objects.filter(match_id=match.id).count() == 3

    # Content-only correction: fix one ball's runs but keep the same outcome.
    fixed = replace(
        batch(ctx, [1], runs=5, innings=2).drafts[0],
        revision=2,
    )
    report = apply_delivery_correction(
        match,
        [LiveInningsBatch(innings_number=2, drafts=(fixed,))],
        reason="provider corrected a ball",
        source=_provider(),
    )
    assert report.result_changed is False
    # Tier-2 rows rebuilt for the correction: the innings total changed
    # (66 -> 65), so each line was recomputed once (revision 2) and the second
    # rebuild of the same payload left revision untouched (idempotent).
    stats = PlayerMatchStat.objects.filter(match_id=match.id)
    assert stats.count() == 3
    # Only lines whose payload changed are bumped (the innings-2 batter); the
    # idempotent re-rebuild of the same payload leaves revision untouched.
    assert set(stats.values_list("revision", flat=True)) == {1, 2}


def test_content_only_correction_appends_correction_applied_event() -> None:
    from apps.live.services.apply import LiveInningsBatch
    from apps.live.services.corrections import apply_delivery_correction

    ctx = make_context()
    match = ctx["match"]
    _finish_t20(ctx)
    result_service.finalise(match)

    fixed = replace(
        batch(ctx, [1], runs=5, innings=2).drafts[0],
        revision=2,
    )
    apply_delivery_correction(
        match,
        [LiveInningsBatch(innings_number=2, drafts=(fixed,))],
        reason="provider corrected a ball",
        source=_provider(),
    )
    events = MatchEventLog.objects.filter(
        match_id=match.id, event_type=event_schemas.CORRECTION_APPLIED
    )
    assert events.count() == 1
    assert events.first().payload["result_changed"] is False
    assert events.first().payload["reason"] == "provider corrected a ball"


def test_reapply_correction_is_idempotent_noop() -> None:
    from apps.live.services.apply import LiveInningsBatch
    from apps.live.services.corrections import apply_delivery_correction

    ctx = make_context()
    _add_wicket_batters(ctx)
    match = ctx["match"]
    _finish_t20(ctx)
    result_service.finalise(match)
    records = [
        LiveInningsBatch(innings_number=2, drafts=tuple(_wicket(ctx, s) for s in range(1, 11)))
    ]

    first = apply_delivery_correction(
        match, records, reason="provider revised the innings", source=_provider()
    )
    assert first.superseded == 10
    events_after_first = MatchEventLog.objects.filter(match_id=match.id).count()

    second = apply_delivery_correction(
        match, records, reason="provider revised the innings", source=_provider()
    )
    assert second.superseded == 0
    assert second.result_changed is False
    assert MatchEventLog.objects.filter(match_id=match.id).count() == events_after_first


# --------------------------------------------------------------------------- #
# Steward admin actions: permission matrix
# --------------------------------------------------------------------------- #


def _request_for(user: object) -> object:
    request = RequestFactory().get("/admin/matches/match/")
    request.user = user
    return request


def test_reopen_and_correction_actions_require_change_permission() -> None:
    from apps.matches.admin import MatchAdmin

    match_admin = MatchAdmin(Match, dj_admin.AdminSite())

    admin_user = get_user_model().objects.create_user(username="root-admin", password="x")
    admin_user.groups.add(Group.objects.get(name="admin"))
    actions = set(match_admin.get_actions(_request_for(admin_user)))
    assert {"reopen_for_correction", "request_manual_correction"} <= actions

    # viewer / operator / data_steward lack matches.change_match -> hidden.
    for role in ("viewer", "operator", "data_steward"):
        user = get_user_model().objects.create_user(username=f"u-{role}", password="x")
        user.groups.add(Group.objects.get(name=role))
        actions = set(match_admin.get_actions(_request_for(user)))
        assert "reopen_for_correction" not in actions
        assert "request_manual_correction" not in actions


def test_apply_manual_correction_service_is_audited() -> None:
    from apps.administration.handlers import on_correction_requested, on_match_reopened
    from apps.administration.models import AuditLog
    from apps.synchronization.services.corrections import apply_manual_correction

    ctx = make_context()
    match = ctx["match"]
    steward = get_user_model().objects.create_user(username="steward-1", password="x")
    row = apply_manual_correction(
        match=match,
        entity_type="match",
        entity_id=match.id,
        field_path="status_detail",
        old_value="old detail",
        new_value="new detail",
        reason="steward edit",
        actor=steward,
    )
    assert DataCorrection.objects.filter(pk=row.pk).exists()
    events = MatchEventLog.objects.filter(
        match_id=match.id, event_type=event_schemas.CORRECTION_APPLIED
    )
    assert events.count() == 1
    assert events.first().payload["entity_type"] == "match"

    # The Phase B audit subscribers record the steward actions.
    on_correction_requested(
        {
            "match_id": str(match.id),
            "entity_type": "match",
            "entity_id": str(match.id),
            "old_value": "old",
            "new_value": "new",
            "actor": "steward-1",
        }
    )
    on_match_reopened({"match_id": str(match.id), "reason": "steward reopen", "actor": "steward-1"})
    audit = AuditLog.objects.filter(
        action__in=(core_events.TOPIC_CORRECTION_REQUESTED, core_events.TOPIC_MATCH_REOPENED)
    )
    assert audit.count() == 2
    assert set(audit.values_list("actor_label", flat=True)) == {"steward-1"}

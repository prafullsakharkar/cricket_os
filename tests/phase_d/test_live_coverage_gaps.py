"""Phase D: branch-coverage gaps in the live ingestion surface.

These tests pin the *uncovered* edges of the live write path, polling
dispatcher, tasks, credentials and the reconciliation/event-log services —
defensive paths, error branches and orchestration entry points that the
functional suites (test_live_apply, test_ingestion_framework,
test_reconciliation) do not reach. They exist to keep the new ``apps/live``
module above the Phase D ≥90% branch target and the reconciliation/event-log
services above ≥95%.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.core.enums import InningsStatus, MatchStatus
from apps.core.errors import Unprocessable
from apps.ingestion.models import IngestionError
from apps.live.services import breaker as brk
from apps.live.services import credentials as creds
from apps.live.services import polling
from apps.live.services.apply import LiveInningsBatch, apply_batch
from apps.matches.models import MatchTeam
from apps.matches.services import event_log
from apps.synchronization.services import reconciliation

from .test_live_apply import ball, batch, make_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolate_breakers() -> None:
    brk.reset_breakers()
    yield
    brk.reset_breakers()


def _provider() -> Any:
    from tests.phase_c.factories import make_provider

    return make_provider("replay")


# --------------------------------------------------------------------------- #
# apply_batch defensive paths
# --------------------------------------------------------------------------- #


def test_apply_requires_exactly_two_teams() -> None:
    ctx = make_context()
    match = ctx["match"]
    # Remove one team: live ingestion must refuse to guess.
    MatchTeam.objects.filter(match=match, team_number=2).delete()
    with pytest.raises(Unprocessable):
        apply_batch(match, [batch(ctx, [1], runs=2)])


def test_explicit_batting_and_bowling_team_ids_are_honoured() -> None:
    ctx = make_context()
    match = ctx["match"]
    records = [
        LiveInningsBatch(
            innings_number=1,
            drafts=(ball(ctx, 1, 2),),
            batting_team_id=ctx["team2"].id,  # type: ignore[attr-defined]
            bowling_team_id=ctx["team1"].id,  # type: ignore[attr-defined]
        )
    ]
    report = apply_batch(match, records)
    innings = match.innings.get(id=report.innings[0])
    assert innings.batting_team_id == ctx["team2"].id  # type: ignore[attr-defined]
    assert innings.bowling_team_id == ctx["team1"].id  # type: ignore[attr-defined]


def test_super_over_batch_defaults_to_first_two_teams() -> None:
    ctx = make_context()
    match = ctx["match"]
    report = apply_batch(
        match,
        [
            LiveInningsBatch(
                innings_number=1,
                drafts=(ball(ctx, 1, 2),),
                is_super_over=True,
                super_over_number=1,
            )
        ],
    )
    innings = match.innings.get(id=report.innings[0])
    assert innings.is_super_over is True
    assert innings.batting_team_id == ctx["team1"].id  # type: ignore[attr-defined]
    assert innings.bowling_team_id == ctx["team2"].id  # type: ignore[attr-defined]


def test_fresh_innings_with_not_started_status_starts_play() -> None:
    ctx = make_context()
    match = ctx["match"]
    from tests.phase_c.factories import make_innings

    make_innings(match, ctx["team1"], ctx["team2"], innings_number=1)  # type: ignore[arg-type]
    assert match.innings.get(innings_number=1).status == InningsStatus.NOT_STARTED

    report = apply_batch(match, [batch(ctx, [1], runs=2)])
    innings = match.innings.get(id=report.innings[0])
    assert innings.status == InningsStatus.IN_PROGRESS


def test_supplied_target_fields_update_existing_innings() -> None:
    ctx = make_context()
    match = ctx["match"]
    apply_batch(match, [batch(ctx, [1, 2], runs=2)])
    records = [
        LiveInningsBatch(
            innings_number=1,
            drafts=(ball(ctx, 3, 1),),
            target=55,
            target_balls=40,
            dls_target=50,
        )
    ]
    apply_batch(match, records)
    innings = match.innings.get(innings_number=1)
    assert innings.target == 55
    assert innings.target_balls == 40
    assert innings.dls_target == 50


def test_delivery_draft_alone_is_rejected() -> None:
    ctx = make_context()
    with pytest.raises(Unprocessable):
        apply_batch(ctx["match"], [ball(ctx, 1, 2)])  # type: ignore[list-item]


def test_empty_batch_skips_checkpoint_advance() -> None:
    ctx = make_context()
    match = ctx["match"]
    report = apply_batch(match, [LiveInningsBatch(innings_number=1, drafts=())])
    assert (report.appended, report.corrected, report.noop) == (0, 0, 0)
    # No delivery seqs exist, so no checkpoint position can be advanced.
    assert report.checkpoint_seq is None


def test_apply_advances_checkpoint_on_commit() -> None:
    ctx = make_context()
    match = ctx["match"]
    report = apply_batch(
        match,
        [batch(ctx, [1, 2], runs=3)],
        provider=_provider(),
        checkpoint_scope=f"match:{match.id}",
    )
    assert report.checkpoint_seq == 2
    assert report.appended == 2


# --------------------------------------------------------------------------- #
# Polling dispatcher edges
# --------------------------------------------------------------------------- #


def test_poll_registers_breaker_and_records_success() -> None:
    ctx = make_context()
    match = ctx["match"]
    breaker = brk.CircuitBreaker("replay:poll", provider="replay", endpoint="poll")
    report = polling.poll_live_match(
        match,
        fetcher=lambda m, s: [batch(ctx, [1], runs=2)],
        source=_provider(),
        provider=_provider(),
        breaker=breaker,
    )
    assert report.polled is True
    assert report.applied == 1
    assert report.changed is True
    assert brk.is_open_for("replay") is False  # breaker registered + closed
    assert breaker.state == brk.CircuitState.CLOSED  # record_success ran


def test_poll_skips_when_lock_already_held() -> None:
    ctx = make_context()
    match = ctx["match"]
    polling.acquire_poll_lock(match.id)
    try:
        report = polling.poll_live_match(match, fetcher=lambda m, s: [])
        assert report.polled is False
        assert report.skipped_reason == "locked"
    finally:
        polling.release_poll_lock(match.id)


def test_poll_reports_breaker_open_raised_by_fetcher() -> None:
    ctx = make_context()
    match = ctx["match"]

    def boom(m: object, s: int | None) -> list[object]:
        raise brk.CircuitOpenError("provider circuit open")

    report = polling.poll_live_match(match, fetcher=boom)
    assert report.polled is True
    assert report.skipped_reason == "breaker_open"
    assert report.stale is True


def test_poll_fetch_failure_without_breaker_still_reports() -> None:
    ctx = make_context()
    match = ctx["match"]

    def boom(m: object, s: int | None) -> list[object]:
        raise ValueError("malformed payload")

    report = polling.poll_live_match(match, fetcher=boom)
    assert report.polled is True
    assert report.skipped_reason == "fetch_failed"
    assert report.stale is True


def test_dispatch_polls_with_fetcher_factory() -> None:
    ctx = make_context()
    match = ctx["match"]

    def factory(m: object) -> object:
        def fetcher(m2: object, since_seq: int | None) -> list[LiveInningsBatch]:
            return [batch(ctx, [1], runs=2)]

        return fetcher

    reports = polling.dispatch_polls(fetcher_factory=factory, provider=_provider(), max_matches=1)
    assert len(reports) == 1
    assert reports[0].applied == 1
    assert reports[0].match_id == str(match.id)


def test_dispatch_polls_with_adapter_factory() -> None:
    ctx = make_context()
    match = ctx["match"]

    class StubLiveAdapter:
        def iter_deliveries(self, provider_match_id: str, since_seq: int | None) -> list[object]:
            assert provider_match_id == "p-1"
            return [batch(ctx, [1], runs=2)]

    match.provider_match_id = "p-1"
    match.save(update_fields=["provider_match_id"])

    reports = polling.dispatch_polls(adapter=StubLiveAdapter(), max_matches=1)
    assert len(reports) == 1
    assert reports[0].applied == 1


# --------------------------------------------------------------------------- #
# Celery tasks entry points
# --------------------------------------------------------------------------- #


def test_poll_live_match_task_missing_and_present() -> None:
    from apps.live.services.tasks import poll_live_match_task

    missing = poll_live_match_task("00000000-0000-0000-0000-000000000099")
    assert missing == {
        "match_id": "00000000-0000-0000-0000-000000000099",
        "polled": False,
        "skipped_reason": "no_match",
    }

    ctx = make_context()
    report = poll_live_match_task(str(ctx["match"].id))
    assert report["polled"] is True
    assert report["applied"] == 0  # _noop_fetcher


def test_dispatch_polling_task() -> None:
    from apps.live.services.tasks import dispatch_polling_task

    ctx = make_context()
    reports = dispatch_polling_task()
    assert len(reports) == 1
    assert reports[0]["match_id"] == str(ctx["match"].id)
    # No fetcher configured in the beat entry: the poll fails gracefully.
    assert reports[0]["skipped_reason"] == "fetch_failed"


@override_settings(LIVE_INGESTION_ENABLED=True)
def test_sync_fixtures_task_without_provider_filter() -> None:
    from apps.live.services.tasks import sync_fixtures_task
    from apps.providers.registry import clear_registry, register_adapter
    from apps.synchronization.services.fixtures import FixtureRecord

    class Stub:
        def list_fixtures(self, window: object) -> list[FixtureRecord]:
            return [
                FixtureRecord(
                    provider_match_id="f-1",
                    format_type="T20",
                    start_time_utc=timezone.now(),
                    teams=("Alpha", "Beta"),
                )
            ]

    clear_registry()
    register_adapter("replay", lambda: Stub())
    provider = _provider()
    # Only the replay provider is active; cricsheet is a seeded inactive row.
    from apps.providers.models import DataProvider

    DataProvider.objects.exclude(id=provider.id).update(is_active=False)

    reports = sync_fixtures_task(days=1)
    assert len(reports) == 1
    assert reports[0]["provider"] == "replay"
    assert reports[0]["created"] == 1


def test_receive_push_unknown_match() -> None:
    from apps.live.services.tasks import receive_push

    result = receive_push(b'{"match_id": "00000000-0000-0000-0000-000000000099", "batches": []}')
    assert result == {"accepted": False, "error": "no_match"}


def test_receive_push_carries_lifecycle_state() -> None:
    from apps.live.services.tasks import receive_push

    ctx = make_context()
    match = ctx["match"]
    envelope = {
        "match_id": str(match.id),
        "state": {"status": "IN_PROGRESS", "state_title": "Day 1", "status_detail": "Tea"},
        "batches": [{"innings_number": 1, "drafts": []}],
    }
    result = receive_push(json.dumps(envelope).encode())
    assert result["accepted"] is True
    match.refresh_from_db()
    assert match.status == MatchStatus.IN_PROGRESS


# --------------------------------------------------------------------------- #
# Credentials edges
# --------------------------------------------------------------------------- #


def test_secret_store_protocol_raises() -> None:
    with pytest.raises(NotImplementedError):
        creds.SecretStore().get_secret("x")


def test_env_store_missing_secret_raises() -> None:
    with pytest.raises(creds.CredentialNotFoundError):
        creds.EnvSecretStore(environ={}).get_secret("no-such-ref")


def test_credential_headers_basic_auth() -> None:
    provider = type(
        "P", (), {"code": "replay", "credential_ref": "replay-provider", "auth_kind": "basic"}
    )()
    headers = creds.credential_headers(provider, environ={"SECRET_REPLAY_PROVIDER": "u:p"})
    assert headers["Authorization"].startswith("Basic ")


def test_safe_credential_none() -> None:
    assert creds.safe_credential(None) == "none"


# --------------------------------------------------------------------------- #
# Event-log edges
# --------------------------------------------------------------------------- #


def test_append_unknown_event_type_raises() -> None:
    ctx = make_context()
    with pytest.raises(ValueError):
        event_log.append_events(ctx["match"].id, [("no.such.event", {})])


def test_events_since_bool_reflects_rows() -> None:
    ctx = make_context()
    match = ctx["match"]
    apply_batch(match, [batch(ctx, [1], runs=2)])
    result = event_log.events_since(match.id, 0)
    assert result.resync_required is False
    assert bool(result) is True


def test_prune_events_with_no_candidates_is_a_noop() -> None:
    ctx = make_context()
    apply_batch(ctx["match"], [batch(ctx, [1], runs=2)])
    report = event_log.prune_events()
    assert report.matches == []
    assert report.events == 0
    assert report.dry_run is True


# --------------------------------------------------------------------------- #
# Reconciliation edges
# --------------------------------------------------------------------------- #


def test_mismatch_without_reporter_is_silent() -> None:
    reconciliation.register_mismatch_reporter(None)
    ctx = make_context()
    match = ctx["match"]
    apply_batch(match, [batch(ctx, [1, 2])])
    report = reconciliation.reconcile_checkpoint_gaps(
        match, provider=_provider(), provider_innings_length=4
    )
    assert report.job is not None
    assert not IngestionError.objects.filter(stage="reconciliation_mismatch").exists()
    reconciliation.register_mismatch_reporter(None)  # restore to the no-op state


def test_gap_report_truthiness() -> None:
    ctx = make_context()
    match = ctx["match"]
    apply_batch(match, [batch(ctx, [1, 2])])
    report = reconciliation.reconcile_checkpoint_gaps(
        match, provider=_provider(), provider_innings_length=4
    )
    assert bool(report) is True


def test_checkpoint_gaps_require_length_or_seq() -> None:
    ctx = make_context()
    with pytest.raises(ValueError):
        reconciliation.reconcile_checkpoint_gaps(ctx["match"], provider=_provider())


def test_checkpoint_gaps_without_provider() -> None:
    ctx = make_context()
    match = ctx["match"]
    apply_batch(match, [batch(ctx, [1, 2])])
    report = reconciliation.reconcile_checkpoint_gaps(
        match, provider=None, provider_innings_length=3
    )
    assert report.missing == frozenset({3})
    assert report.job is not None


def test_engine_figures_tolerate_missing_summary_and_result() -> None:
    from tests.phase_c.factories import make_innings

    ctx = make_context()
    match = ctx["match"]
    # An innings with no summary row and a match with no result row: the
    # engine-figures walker must tolerate both (reverse one-to-one raises).
    make_innings(match, ctx["team1"], ctx["team2"], innings_number=1)  # type: ignore[arg-type]
    assert not hasattr(match.innings.first(), "summary")

    job = reconciliation.reconcile_state(
        match,
        provider_card={
            "summaries": [{"innings_number": 1, "runs": 5, "wickets": 0, "extras": {}}],
            "result": {"result_type": "WIN"},
        },
        provider=_provider(),
    )
    assert job is not None
    assert "innings.1" in job.differences

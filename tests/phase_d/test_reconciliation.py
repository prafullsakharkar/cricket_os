"""Phase D group 4: reconciliation jobs (DATA_INGESTION §4.6).

Three kinds, each idempotent and never auto-correcting silently:

* checkpoint/gap — stored seq set vs the provider's innings length;
* state — engine figures vs the provider's final scorecard for a completed
  match;
* fixtures — matches in the window missing from the provider, or vice versa.

Every mismatch opens a ``ReconciliationJob`` (reused across re-runs) and
writes a redacted ``IngestionError`` (``stage=reconciliation_mismatch``).
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from apps.core.enums import MatchStatus
from apps.ingestion.models import IngestionError
from apps.synchronization.models import ReconciliationJob, SyncCheckpoint
from apps.synchronization.services import reconciliation

from .test_live_apply import _finish_t20, batch, make_context

pytestmark = pytest.mark.django_db


def _provider():
    from tests.phase_c.factories import make_provider

    return make_provider("replay")


def _open_jobs() -> int:
    return ReconciliationJob.objects.filter(status=ReconciliationJob.STATUS_OPEN).count()


# --------------------------------------------------------------------------- #
# Checkpoint / gap reconciliation
# --------------------------------------------------------------------------- #


def test_gap_is_detected_and_reported() -> None:
    ctx = make_context()
    match = ctx["match"]
    provider = _provider()
    from apps.live.services.apply import apply_batch

    apply_batch(match, [batch(ctx, [1, 2, 3, 4, 6])])  # seq 5 never arrived
    SyncCheckpoint.objects.create(
        provider=provider,
        scope_key=f"match:{match.id}",
        last_delivery_seq=4,
    )

    report = reconciliation.reconcile_checkpoint_gaps(
        match, provider=provider, provider_innings_length=6
    )
    assert report.missing == frozenset({5})  # seq 6 arrived; only 5 is missing
    assert report.job is not None
    assert report.job.scope_key == f"checkpoint:{match.id}:1"
    assert report.job.differences == {"missing_seq": [5]}
    assert report.job.observed_state["checkpoint_last_delivery_seq"] == 4
    assert IngestionError.objects.filter(stage="reconciliation_mismatch").exists()


def test_no_gap_returns_no_job() -> None:
    ctx = make_context()
    match = ctx["match"]
    from apps.live.services.apply import apply_batch

    apply_batch(match, [batch(ctx, [1, 2, 3])])
    report = reconciliation.reconcile_checkpoint_gaps(
        match, provider=_provider(), provider_innings_length=3
    )
    assert report.missing == frozenset()
    assert report.job is None
    assert _open_jobs() == 0


def test_gap_reconciliation_is_idempotent() -> None:
    ctx = make_context()
    match = ctx["match"]
    from apps.live.services.apply import apply_batch

    apply_batch(match, [batch(ctx, [1, 2])])
    reconciliation.reconcile_checkpoint_gaps(match, provider=_provider(), provider_innings_length=4)
    reconciliation.reconcile_checkpoint_gaps(match, provider=_provider(), provider_innings_length=4)
    assert _open_jobs() == 1
    assert ReconciliationJob.objects.filter(scope_key=f"checkpoint:{match.id}:1").count() == 1


def test_provider_seq_set_is_honoured() -> None:
    ctx = make_context()
    match = ctx["match"]
    from apps.live.services.apply import apply_batch

    apply_batch(match, [batch(ctx, [1, 3, 5])])
    report = reconciliation.reconcile_checkpoint_gaps(
        match, provider=_provider(), provider_seq=[1, 2, 3, 4, 5]
    )
    assert report.missing == frozenset({2, 4})


# --------------------------------------------------------------------------- #
# State reconciliation
# --------------------------------------------------------------------------- #


def test_state_mismatch_creates_job_and_dlq_row() -> None:
    ctx = make_context()
    match = ctx["match"]
    _finish_t20(ctx)
    from apps.matches.services.result_service import finalise

    finalise(match)
    match.refresh_from_db()

    provider_card = {
        "summaries": [
            {"innings_number": 1, "runs": 60, "wickets": 0, "extras": {}},
            {"innings_number": 2, "runs": 66, "wickets": 0, "extras": {}},
        ],
        "result": {
            "result_type": "WIN",
            "winner_team_id": "00000000-0000-0000-0000-000000000099",
            "margin_type": "wickets",
            "margin_value": 8,
        },
    }
    job = reconciliation.reconcile_state(match, provider_card=provider_card, provider=_provider())
    assert job is not None
    assert job.scope_key == f"state:{match.id}"
    assert "result.winner_team_id" in job.differences
    assert IngestionError.objects.filter(stage="reconciliation_mismatch").exists()


def test_state_match_returns_no_job() -> None:
    ctx = make_context()
    match = ctx["match"]
    _finish_t20(ctx)
    from apps.matches.services.result_service import finalise

    finalise(match)
    match.refresh_from_db()
    ours = reconciliation._engine_figures(match)

    assert reconciliation.reconcile_state(match, provider_card=ours) is None
    assert _open_jobs() == 0


def test_state_reconciliation_is_idempotent() -> None:
    ctx = make_context()
    match = ctx["match"]
    _finish_t20(ctx)
    from apps.matches.services.result_service import finalise

    finalise(match)
    match.refresh_from_db()
    bad_card = {
        "summaries": [{"innings_number": 1, "runs": 1, "wickets": 0, "extras": {}}],
        "result": {},
    }
    reconciliation.reconcile_state(match, provider_card=bad_card)
    reconciliation.reconcile_state(match, provider_card=bad_card)
    assert _open_jobs() == 1


def test_resolve_and_ignore() -> None:
    ctx = make_context()
    match = ctx["match"]
    from apps.live.services.apply import apply_batch

    apply_batch(match, [batch(ctx, [1])])
    job = reconciliation.reconcile_checkpoint_gaps(
        match, provider=_provider(), provider_innings_length=3
    ).job
    assert job is not None

    resolved = reconciliation.resolve_job(job)
    assert resolved.status == ReconciliationJob.STATUS_RESOLVED
    assert resolved.resolved_at is not None
    # Resolving again is a no-op.
    assert reconciliation.resolve_job(resolved).resolved_at == resolved.resolved_at
    # The resolved row is left untouched.
    resolved.refresh_from_db()
    assert resolved.status == ReconciliationJob.STATUS_RESOLVED

    # A still-present gap is re-flagged on the next run (the problem persists):
    # a fresh open job appears while the resolved one stays resolved.
    job2 = reconciliation.reconcile_checkpoint_gaps(
        match, provider=_provider(), provider_innings_length=3
    ).job
    assert job2 is not None
    assert job2.status == ReconciliationJob.STATUS_OPEN
    assert job2.pk != resolved.pk
    assert ReconciliationJob.objects.filter(scope_key=f"checkpoint:{match.id}:1").count() == 2


# --------------------------------------------------------------------------- #
# Fixture reconciliation
# --------------------------------------------------------------------------- #


def test_fixture_reconciliation_both_directions() -> None:
    from tests.phase_c.factories import make_match

    provider = _provider()
    window_start = timezone.now() - datetime.timedelta(days=1)
    window_end = timezone.now() + datetime.timedelta(days=1)
    match_a = make_match(
        start_time_utc=window_start + datetime.timedelta(hours=1),
        source_provider_id=provider.id,
        provider_match_id="p-1",
        status=MatchStatus.SCHEDULED,
    )
    make_match(
        start_time_utc=window_start + datetime.timedelta(hours=2),
        source_provider_id=provider.id,
        provider_match_id="p-2",
        status=MatchStatus.SCHEDULED,
    )
    # Provider reports p-1 and p-3: p-2 missing from the provider, p-3 from us.
    report = reconciliation.reconcile_fixtures(
        provider,
        window_start=window_start,
        window_end=window_end,
        provider_fixtures=["p-1", "p-3"],
    )
    assert report.missing_from_us == ("p-3",)
    assert report.missing_from_provider == ("p-2",)
    assert len(report.jobs) == 2
    assert match_a.status == MatchStatus.SCHEDULED  # never deleted/transitioned


def test_fixture_reconciliation_in_sync_returns_no_jobs() -> None:
    from tests.phase_c.factories import make_match

    provider = _provider()
    window_start = timezone.now() - datetime.timedelta(days=1)
    window_end = timezone.now() + datetime.timedelta(days=1)
    make_match(
        start_time_utc=window_start + datetime.timedelta(hours=1),
        source_provider_id=provider.id,
        provider_match_id="p-1",
    )
    report = reconciliation.reconcile_fixtures(
        provider,
        window_start=window_start,
        window_end=window_end,
        provider_fixtures=["p-1"],
    )
    assert bool(report) is False
    assert report.jobs == ()

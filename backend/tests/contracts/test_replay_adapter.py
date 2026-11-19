"""ReplayAdapter tests: the provider-adapter contract kit + fault injection.

The fault matrix (DATA_INGESTION §4.3/§4.4) is exercised against a recorded
match: duplicate (idempotency), delayed, reordered, dropped (gap), correction
after completion, ``429`` Retry-After, ``5xx``, timeout, malformed payloads,
unknown status enum, outage window, virtual-clock pacing, and the prod guard.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from tests.contracts.provider_adapter_kit import ProviderAdapterKit

from apps.core.errors import RateLimited, UpstreamUnavailable
from apps.providers.adapters.replay import (
    ReplayAdapter,
    ReplayClock,
    ReplayDisabledInProduction,
    ReplayFaults,
    replay_available,
)

pytestmark = pytest.mark.django_db


def _recorded_match() -> object:
    """A match with stored deliveries (Phase C data the adapter replays)."""
    from tests.phase_d.test_live_apply import _finish_t20, make_context

    ctx = make_context()
    _finish_t20(ctx)
    return ctx["match"]


def _seqs(batches: list[object]) -> list[int]:
    return [d.delivery_seq for b in batches for d in b.drafts]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Contract kit
# --------------------------------------------------------------------------- #


class TestReplayAdapterContract(ProviderAdapterKit):
    def make_adapter(self) -> ReplayAdapter:
        return ReplayAdapter()

    def make_recorded_match(self) -> object:
        return _recorded_match()


# --------------------------------------------------------------------------- #
# Virtual clock + pacing
# --------------------------------------------------------------------------- #


def test_clock_advances_with_speed_multiplier() -> None:
    clock = ReplayClock(epoch=100.0, speed=10.0)
    assert clock.now() == 100.0
    clock.advance(2.0)  # 2 virtual seconds × speed 10 = 20 real-equivalent
    assert clock.now() == 120.0


def test_paced_replay_releases_by_virtual_time() -> None:
    match = _recorded_match()
    clock = ReplayClock(epoch=0.0)
    adapter = ReplayAdapter(clock=clock, paced=True, release_interval=10.0)
    first = list(adapter.iter_deliveries(str(match.id), None))
    # seq 1 of each innings is released at t=0; nothing later.
    assert sorted(set(_seqs(first))) == [1]
    clock.advance(10.0)
    second = list(adapter.iter_deliveries(str(match.id), 1))
    assert sorted(set(_seqs(second))) == [2]


def test_replay_unavailable_in_prod() -> None:
    with override_settings(ENVIRONMENT="prod"):
        assert replay_available() is False
        with pytest.raises(ReplayDisabledInProduction):
            ReplayAdapter()


# --------------------------------------------------------------------------- #
# Fault injection
# --------------------------------------------------------------------------- #


def test_duplicate_delivery_is_served_twice() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(duplicate=(11,)))  # 11 is innings-2-only
    first = list(adapter.iter_deliveries(str(match.id), None))
    assert _seqs(first).count(11) == 1  # served once now…
    second = list(adapter.iter_deliveries(str(match.id), max(_seqs(first))))
    assert _seqs(second) == [11]  # …and again on the next poll


def test_delayed_delivery_held_for_n_calls() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(delayed={11: 1}))  # 11 is innings-2-only
    first = list(adapter.iter_deliveries(str(match.id), None))
    assert 11 not in _seqs(first)
    second = list(adapter.iter_deliveries(str(match.id), 10))  # resume past what was served
    assert 11 in _seqs(second)


def test_reordered_first_window() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(reordered=(2, 1, 3)))
    first = list(adapter.iter_deliveries(str(match.id), None))
    assert _seqs(first)[:3] == [2, 1, 3]


def test_dropped_delivery_never_served() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(dropped=(9,)))
    seqs = set()
    since = None
    while True:
        batches = list(adapter.iter_deliveries(str(match.id), since))
        if not batches:
            break
        seqs.update(_seqs(batches))
        since = max(seqs)
    assert 9 not in seqs
    assert 11 in seqs  # the innings-2 tail still arrives
    assert 1 in seqs


def test_correction_after_completion_serves_revision_2() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(correction=(5,)))
    first = list(adapter.iter_deliveries(str(match.id), None))
    assert all(d.revision == 1 for d in first[0].drafts)
    last = max(_seqs(first))
    # Stream fully consumed → the revision arrives on the next poll.
    revision = list(adapter.iter_deliveries(str(match.id), last))
    assert revision
    revised = [d for b in revision for d in b.drafts if d.delivery_seq == 5]
    assert revised and revised[0].revision == 2


def test_retry_after_budget_emits_rate_limited() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(retry_after=1))
    with pytest.raises(RateLimited) as exc:
        list(adapter.iter_deliveries(str(match.id), None))
    assert exc.value.retry_after == 15
    batches = list(adapter.iter_deliveries(str(match.id), None))
    assert batches  # recovered


def test_http_5xx_budget_emits_upstream_unavailable() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(http_5xx=2))
    with pytest.raises(UpstreamUnavailable):
        list(adapter.iter_deliveries(str(match.id), None))
    with pytest.raises(UpstreamUnavailable):
        list(adapter.iter_deliveries(str(match.id), None))
    assert list(adapter.iter_deliveries(str(match.id), None))


def test_timeout_budget_emits_timeout() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(timeout=1))
    with pytest.raises(TimeoutError):
        list(adapter.iter_deliveries(str(match.id), None))
    assert list(adapter.iter_deliveries(str(match.id), None))


def test_outage_window_then_recovery() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(outage_calls=3))
    for _ in range(3):
        with pytest.raises(UpstreamUnavailable):
            list(adapter.iter_deliveries(str(match.id), None))
    assert list(adapter.iter_deliveries(str(match.id), None))


def test_malformed_draft_has_invalid_enum() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(malformed=(4,)))
    batches = list(adapter.iter_deliveries(str(match.id), None))
    malformed = [d for b in batches for d in b.drafts if d.delivery_seq == 4]
    assert malformed
    assert malformed[0].dismissal_type == "NOT_A_DISMISSAL"


def test_unknown_status_via_get_state() -> None:
    match = _recorded_match()
    adapter = ReplayAdapter(faults=ReplayFaults(unknown_status="TEAPOT"))
    state = adapter.get_state(str(match.id))
    assert state.status == "TEAPOT"


def test_slow_fault_advances_clock() -> None:
    match = _recorded_match()
    clock = ReplayClock()
    adapter = ReplayAdapter(clock=clock, faults=ReplayFaults(slow_seconds=2.0))
    list(adapter.iter_deliveries(str(match.id), None))
    assert clock.now() == 2.0


def test_replay_drives_live_pipeline_to_completion() -> None:
    """Acceptance: a replayed match produces a complete event log."""
    from tests.phase_c.factories import make_match, make_match_teams, make_provider
    from tests.phase_d.test_live_apply import _finish_t20
    from tests.phase_d.test_live_apply import make_context as make_source_context

    from apps.deliveries.models import Delivery
    from apps.live.services.apply import apply_batch
    from apps.matches.services.result_service import finalise

    source_ctx = make_source_context()
    _finish_t20(source_ctx)
    source = source_ctx["match"]

    target = make_match(status="SCHEDULED")
    make_match_teams(target, source_ctx["team1"], source_ctx["team2"])
    provider = make_provider("replay")

    adapter = ReplayAdapter()
    since: int | None = None
    polls = 0
    while True:
        batches = list(adapter.iter_deliveries(str(source.id), since))
        if not batches:
            break
        report = apply_batch(
            target, batches, source=provider, checkpoint_scope=f"match:{target.id}"
        )
        seqs = [d.delivery_seq for b in batches for d in b.drafts]
        since = max(seqs)
        polls += 1
        assert report.changed is True

    assert polls >= 1
    assert Delivery.objects.filter(match_id=target.id, is_current=True).count() == 21
    final = finalise(target)
    assert final.completed_now is True
    assert final.winner_team_id == str(source_ctx["team2"].id)
    # The event log is complete: both innings were replayed and the match
    # completed exactly once.
    from apps.matches.models import MatchEventLog

    types = list(
        MatchEventLog.objects.filter(match_id=target.id)
        .order_by("seq")
        .values_list("event_type", flat=True)
    )
    assert types.count("match.completed") == 1
    assert "innings.updated" in types


def test_missing_match_raises_replay_error() -> None:
    from apps.providers.adapters.replay import ReplayAdapterError

    adapter = ReplayAdapter()
    with pytest.raises(ReplayAdapterError):
        list(adapter.iter_deliveries("00000000-0000-0000-0000-000000000000", None))


def test_capabilities_declare_replay_fake() -> None:
    adapter = ReplayAdapter()
    caps = adapter.capabilities()
    assert caps.ball_by_ball is True
    assert caps.push is False
    assert "replay" in caps.features

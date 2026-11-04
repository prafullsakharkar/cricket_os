"""Phase D group 5: live ingestion framework.

Unit (virtual clock, no I/O): backoff/jitter + Retry-After, circuit breaker,
HMAC verification, timestamp replay window, credential redaction.
Integration (real Postgres): polling with a TTL lock + breaker-aware skip, the
push receiver view (signature failure, replay, flood, flag off), and the
push task writing through the identical apply_batch pipeline.
"""

from __future__ import annotations

import json
import time

import pytest
from django.core.cache import cache
from django.test import Client, override_settings

from apps.core import metrics
from apps.core.enums import MatchStatus
from apps.live.services import backoff, polling, receiver
from apps.live.services import breaker as brk
from apps.live.services import credentials as creds
from apps.matches.models import MatchEventLog

from .test_live_apply import batch, make_context

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Backoff
# --------------------------------------------------------------------------- #


def test_backoff_grows_exponentially_with_jitter() -> None:
    policy = backoff.BackoffPolicy(base_seconds=5, jitter=False)
    assert backoff.compute_backoff(0, policy=policy) == 5
    assert backoff.compute_backoff(1, policy=policy) == 10
    assert backoff.compute_backoff(2, policy=policy) == 20
    assert backoff.compute_backoff(3, policy=policy) == 40


def test_backoff_respects_jitter_bounds() -> None:
    policy = backoff.BackoffPolicy(base_seconds=5, jitter=True)
    for attempt in range(6):
        value = backoff.compute_backoff(attempt, policy=policy)
        assert value >= 5 * (2**attempt)
        assert value <= 5 * (2**attempt) + 4


def test_retry_after_wins_over_exponential() -> None:
    policy = backoff.BackoffPolicy(base_seconds=5)
    assert backoff.compute_backoff(0, retry_after=2, policy=policy) == 2
    assert backoff.compute_backoff(5, retry_after=120, policy=policy) == 120


def test_backoff_is_bounded() -> None:
    policy = backoff.BackoffPolicy(base_seconds=5, jitter=False)
    assert backoff.compute_backoff(30, policy=policy) <= backoff.MAX_BACKOFF_SECONDS


# --------------------------------------------------------------------------- #
# Circuit breaker (virtual clock)
# --------------------------------------------------------------------------- #


class Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def __call__(self) -> float:
        return self.t


def _breaker(clock: Clock, **overrides: object) -> brk.CircuitBreaker:
    from dataclasses import replace

    config = replace(
        brk.BreakerConfig(failure_threshold=3, success_threshold=2, recovery_timeout=60.0),
        **overrides,  # type: ignore[arg-type]
    )
    return brk.CircuitBreaker("test", config=config, now=clock, provider="replay", endpoint="match")


def test_breaker_opens_after_failure_threshold() -> None:
    clock = Clock()
    b = _breaker(clock)
    assert b.state == brk.CircuitState.CLOSED
    b.record_failure()
    b.record_failure()
    assert b.state == brk.CircuitState.CLOSED
    b.record_failure()
    assert b.state == brk.CircuitState.OPEN
    with pytest.raises(brk.CircuitOpenError):
        b.allow()


def test_breaker_success_in_closed_resets_failures() -> None:
    clock = Clock()
    b = _breaker(clock)
    b.record_failure()
    b.record_failure()
    b.record_success()  # resets the failure counter
    b.record_failure()
    b.record_failure()
    assert b.state == brk.CircuitState.CLOSED  # reset worked
    b.record_failure()
    assert b.state == brk.CircuitState.OPEN  # threshold reached again


def test_breaker_recovers_to_half_open_then_closed() -> None:
    clock = Clock()
    b = _breaker(clock, success_threshold=1)
    b.record_failure()
    b.record_failure()
    b.record_failure()
    assert b.state == brk.CircuitState.OPEN
    assert b.state_code() == brk.STATE_OPEN
    clock.advance(59.0)
    assert b.state == brk.CircuitState.OPEN  # not yet
    clock.advance(1.1)
    assert b.state == brk.CircuitState.HALF_OPEN
    assert b.allow() is True  # the probe
    b.record_success()
    assert b.state == brk.CircuitState.CLOSED
    assert b.state_code() == brk.STATE_CLOSED


def test_breaker_half_open_failure_reopens() -> None:
    clock = Clock()
    b = _breaker(clock, success_threshold=1)
    for _ in range(3):
        b.record_failure()
    clock.advance(61.0)
    assert b.state == brk.CircuitState.HALF_OPEN
    b.record_failure()
    assert b.state == brk.CircuitState.OPEN


def test_breaker_metric_reflects_state() -> None:
    metrics.reset()
    clock = Clock()
    b = _breaker(clock)
    b.record_failure()
    b.record_failure()
    b.record_failure()
    assert "circuit_breaker_state" in metrics.render()


def test_breaker_reset() -> None:
    clock = Clock()
    b = _breaker(clock)
    b.record_failure()
    b.record_failure()
    b.record_failure()
    assert b.state == brk.CircuitState.OPEN
    b.reset()
    assert b.state == brk.CircuitState.CLOSED


# --------------------------------------------------------------------------- #
# Credentials + redaction
# --------------------------------------------------------------------------- #


def test_env_secret_store_reads_at_call_time() -> None:
    environ = {"SECRET_REPLAY_PROVIDER": "s3cret-value"}
    store = creds.EnvSecretStore(environ=environ)
    assert store.get_secret("replay-provider") == "s3cret-value"
    assert store.get_secret("REPLAY_PROVIDER") == "s3cret-value"


def test_missing_credential_ref_fails_loudly() -> None:
    with pytest.raises(creds.CredentialNotFoundError):
        creds.get_credential(type("P", (), {"code": "anon"})())


def test_credential_never_renders_secret() -> None:
    credential = creds.Credential(credential_ref="replay", value="hunter2")
    assert "hunter2" not in str(credential)
    assert "hunter2" not in creds.safe_credential(credential)


def test_credential_headers_bearer_and_redaction() -> None:
    provider = type(
        "P", (), {"code": "replay", "credential_ref": "replay-provider", "auth_kind": "bearer"}
    )()
    headers = creds.credential_headers(provider, environ={"SECRET_REPLAY_PROVIDER": "tok"})
    assert headers == {"Authorization": "Bearer tok"}
    # Redaction masks credentials in any serialised payload (DLQ etc).
    masked = creds.redact({"Authorization": "Bearer tok", "api_key": "abc", "match_id": "m1"})
    assert masked == {"Authorization": "[REDACTED]", "api_key": "[REDACTED]", "match_id": "m1"}


# --------------------------------------------------------------------------- #
# Polling
# --------------------------------------------------------------------------- #


def test_cadence_is_adaptive_by_status() -> None:
    live = type("M", (), {"status": MatchStatus.IN_PROGRESS})()
    toss = type("M", (), {"status": MatchStatus.TOSS})()
    scheduled = type("M", (), {"status": MatchStatus.SCHEDULED})()
    assert polling.cadence_for(live, base=30) == 30
    assert polling.cadence_for(toss, base=30) == 60
    assert polling.cadence_for(scheduled, base=30) == 180


def test_ttl_lock_deduplicates_overlapping_polls() -> None:
    cache.clear()
    match_id = "00000000-0000-0000-0000-000000000001"
    assert polling.acquire_poll_lock(match_id) is True
    assert polling.acquire_poll_lock(match_id) is False
    polling.release_poll_lock(match_id)
    assert polling.acquire_poll_lock(match_id) is True
    polling.release_poll_lock(match_id)


def test_poll_applies_batches_and_returns_report() -> None:
    ctx = make_context()
    match = ctx["match"]
    records = [batch(ctx, [1, 2, 3], runs=4)]
    report = polling.poll_live_match(match, fetcher=lambda m, s: records)
    assert report.polled is True
    assert report.applied == 3
    assert report.changed is True
    match.refresh_from_db()
    assert match.last_ingested_at is not None


def test_empty_poll_does_not_stamp_freshness() -> None:
    ctx = make_context()
    match = ctx["match"]
    polling.poll_live_match(match, fetcher=lambda m, s: [])
    match.refresh_from_db()
    assert match.last_ingested_at is None


def test_poll_skips_when_breaker_open() -> None:
    ctx = make_context()
    match = ctx["match"]
    clock = Clock()
    b = _breaker(clock)
    b.record_failure()
    b.record_failure()
    b.record_failure()  # OPEN
    report = polling.poll_live_match(match, fetcher=lambda m, s: [], breaker=b)
    assert report.skipped_reason == "breaker_open"
    assert report.stale is True
    assert MatchEventLog.objects.filter(match_id=match.id).count() == 0


def test_poll_reports_fetch_failure_and_records_breaker_failure() -> None:
    ctx = make_context()
    match = ctx["match"]
    clock = Clock()
    b = _breaker(clock, failure_threshold=1)

    def boom(match: object, since_seq: int | None) -> list[object]:
        raise ConnectionError("provider down")

    report = polling.poll_live_match(match, fetcher=boom, breaker=b)
    assert report.skipped_reason == "fetch_failed"
    assert report.stale is True
    assert b.state == brk.CircuitState.OPEN  # failure recorded


def test_live_matches_filters_statuses() -> None:
    from tests.phase_c.factories import make_match

    make_match(status=MatchStatus.IN_PROGRESS)
    make_match(status=MatchStatus.SCHEDULED)
    make_match(status=MatchStatus.COMPLETED)
    live = polling.live_matches()
    assert len(live) == 1
    assert live[0].status == MatchStatus.IN_PROGRESS


# --------------------------------------------------------------------------- #
# Push receiver
# --------------------------------------------------------------------------- #


def test_signature_roundtrip_and_tamper() -> None:
    payload = b'{"match_id": "m1"}'
    sig = receiver.compute_signature(payload, "secret")
    assert receiver.verify_signature(payload, sig, "secret") is True
    assert receiver.verify_signature(b"tampered", sig, "secret") is False
    assert receiver.verify_signature(payload, "deadbeef", "secret") is False


def test_timestamp_replay_window() -> None:
    now = 1_000_000.0
    assert receiver.verify_timestamp(str(now), now=now, window_seconds=300) is True
    assert receiver.verify_timestamp(str(now + 299), now=now, window_seconds=300) is True
    assert receiver.verify_timestamp(str(now + 301), now=now, window_seconds=300) is False
    assert receiver.verify_timestamp("not-a-number", now=now) is False


def _push_body() -> bytes:
    ctx = make_context()
    match = ctx["match"]
    drafts = [batch(ctx, [1], runs=2).drafts[0]]
    import dataclasses

    draft = drafts[0]
    envelope = {
        "match_id": str(match.id),
        "batches": [{"innings_number": 1, "drafts": [dataclasses.asdict(draft)]}],
    }
    return json.dumps(envelope, default=str).encode()


def _push_headers(body: bytes) -> dict[str, str]:
    now = int(time.time())
    sig = receiver.compute_signature(body, "s3cret")
    return {"X-Signature": sig, "X-Timestamp": str(now)}


@override_settings(
    LIVE_PUSH_RECEIVER_ENABLED=True,
    LIVE_PUSH_HMAC_SECRET="s3cret",
    LIVE_PUSH_REPLAY_WINDOW_SECONDS=300,
)
def test_push_receiver_accepts_and_enqueues() -> None:
    cache.clear()
    body = _push_body()
    captured: list[bytes] = []

    from apps.live.services.receiver import LivePushReceiverView

    LivePushReceiverView.enqueue = captured.append
    client = Client()
    response = client.post(
        "/ingest/live/push", data=body, headers=_push_headers(body), content_type="application/json"
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert captured == [body]
    LivePushReceiverView.enqueue = None


@override_settings(LIVE_PUSH_RECEIVER_ENABLED=True, LIVE_PUSH_HMAC_SECRET="s3cret")
def test_push_receiver_rejects_bad_signature_and_replay() -> None:
    cache.clear()
    body = _push_body()
    client = Client()
    # Bad signature.
    response = client.post(
        "/ingest/live/push",
        data=body,
        headers={"X-Signature": "deadbeef", "X-Timestamp": str(int(time.time()))},
        content_type="application/json",
    )
    assert response.status_code == 401
    # Replayed (stale) timestamp.
    headers = _push_headers(body)
    headers["X-Timestamp"] = str(int(time.time()) - 600)
    response = client.post(
        "/ingest/live/push", data=body, headers=headers, content_type="application/json"
    )
    assert response.status_code == 401


@override_settings(LIVE_PUSH_RECEIVER_ENABLED=True, LIVE_PUSH_HMAC_SECRET="s3cret")
def test_push_receiver_rate_limits_flood() -> None:
    cache.clear()
    body = _push_body()
    client = Client()
    ok = 0
    for _ in range(70):
        response = client.post(
            "/ingest/live/push",
            data=body,
            headers=_push_headers(body),
            content_type="application/json",
        )
        if response.status_code == 202:
            ok += 1
    assert ok <= 60  # limit 60/min per IP
    assert response.status_code == 429


@override_settings(LIVE_PUSH_RECEIVER_ENABLED=False)
def test_push_receiver_404_when_flag_off() -> None:
    client = Client()
    response = client.post(
        "/ingest/live/push",
        data=b"{}",
        headers={"X-Signature": "x", "X-Timestamp": str(int(time.time()))},
        content_type="application/json",
    )
    assert response.status_code == 404


@override_settings(LIVE_PUSH_RECEIVER_ENABLED=True, LIVE_PUSH_HMAC_SECRET="s3cret")
def test_push_task_writes_through_apply_batch() -> None:
    from apps.live.services.tasks import receive_push

    ctx = make_context()
    match = ctx["match"]
    import dataclasses

    envelope = {
        "match_id": str(match.id),
        "batches": [
            {"innings_number": 1, "drafts": [dataclasses.asdict(batch(ctx, [1], runs=3).drafts[0])]}
        ],
    }
    result = receive_push(json.dumps(envelope, default=str).encode())
    assert result["accepted"] is True
    assert result["applied"] == 1
    match.refresh_from_db()
    assert match.last_ingested_at is not None
    assert MatchEventLog.objects.filter(match_id=match.id).count() >= 1


def test_receive_push_missing_match_id() -> None:
    from apps.live.services.tasks import receive_push

    result = receive_push(b'{"batches": []}')
    assert result == {"accepted": False, "error": "missing_match_id"}

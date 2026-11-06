"""Token-bucket throttling (API_SPEC §7, PERFORMANCE §10, SECURITY §2).

* bucket maths with a virtual clock (burst, refill, ``Retry-After``);
* per-scope rates — the ``deliveries`` scope is stricter than ``default``;
* per-identity isolation; per-API-key buckets via the ``KeyResolver`` hook;
* fail-open: a broken bucket store degrades to the per-process fallback + a
  metric, never an error;
* DRF integration: ``429`` ``rate_limited`` envelope with ``Retry-After``,
  applied before the cache lookup so cache hits are throttled too;
* spoofed ``X-Forwarded-For`` cannot switch a client to another bucket;
* :class:`ConnectionLimiter` primitives for the SSE/WS connect endpoints.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings

from apps.core import metrics
from apps.core.throttling import (
    ConnectionLimiter,
    HeaderKeyResolver,
    InProcessBucketStore,
    RateLimiter,
    RedisBucketStore,
    TokenBucket,
    configure_limiter,
)
from tests.phase_c.factories import make_match, make_match_teams, make_team

pytestmark = pytest.mark.django_db


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------------- #
# Bucket maths (virtual clock)
# --------------------------------------------------------------------------- #


def test_fresh_bucket_allows_exactly_burst() -> None:
    clock = FakeClock()
    bucket = TokenBucket.fresh(rate=1.0, burst=5.0, now=clock())
    for _ in range(5):
        allowed, retry, bucket = bucket.try_consume(clock(), 1.0)
        assert allowed
        assert retry is None
    allowed, retry, _ = bucket.try_consume(clock(), 1.0)
    assert not allowed
    assert retry == 1.0


def test_bucket_refills_over_time() -> None:
    clock = FakeClock()
    bucket = TokenBucket.fresh(rate=2.0, burst=10.0, now=clock())
    for _ in range(10):
        allowed, _, bucket = bucket.try_consume(clock(), 1.0)
        assert allowed
    # 3 seconds at 2/s refills 6 tokens (burst 10).
    clock.advance(3.0)
    bucket = bucket.refilled(clock())
    assert bucket.tokens == 6.0


def test_retry_after_is_ceil_of_missing_tokens() -> None:
    clock = FakeClock()
    bucket = TokenBucket.fresh(rate=2.0, burst=2.0, now=clock())
    allowed, _, bucket = bucket.try_consume(clock(), 1.0)
    assert allowed
    allowed, _, bucket = bucket.try_consume(clock(), 1.0)
    assert allowed
    allowed, retry, _ = bucket.try_consume(clock(), 1.0)
    assert not allowed
    assert retry == 1.0  # one missing token at 2/s -> ceil(0.5) = 1


def test_fractional_refill_accumulates() -> None:
    clock = FakeClock()
    bucket = TokenBucket.fresh(rate=1.0, burst=5.0, now=clock())
    for _ in range(5):
        _, _, bucket = bucket.try_consume(clock(), 1.0)
    clock.advance(0.5)
    bucket = bucket.refilled(clock())
    assert bucket.tokens == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# RateLimiter
# --------------------------------------------------------------------------- #


def _limiter(
    clock: FakeClock,
    *,
    rates: dict[str, tuple[float, float]] | None = None,
    store: Any = None,
    resolver: Any = None,
) -> RateLimiter:
    return RateLimiter(
        store=store,
        rates=rates or {"default": (1.0, 3.0), "deliveries": (1.0, 1.0)},
        resolver=resolver,
        clock=clock,
    )


def test_limiter_denies_after_burst_and_reports_retry_after() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    for _ in range(3):
        allowed, retry = limiter.allow("default", "ip:1.1.1.1")
        assert allowed
        assert retry is None
    allowed, retry = limiter.allow("default", "ip:1.1.1.1")
    assert not allowed
    assert retry == 1.0
    # After the wait the bucket refills.
    clock.advance(1.0)
    allowed, _ = limiter.allow("default", "ip:1.1.1.1")
    assert allowed


def test_scopes_are_independent() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    allowed, _ = limiter.allow("deliveries", "ip:1.1.1.1")
    assert allowed  # deliveries burst is 1
    allowed, _ = limiter.allow("deliveries", "ip:1.1.1.1")
    assert not allowed
    # The default scope's bucket is untouched.
    allowed, _ = limiter.allow("default", "ip:1.1.1.1")
    assert allowed


def test_identities_are_isolated() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.allow("default", "ip:1.1.1.1")
    allowed, _ = limiter.allow("default", "ip:2.2.2.2")
    assert allowed


def test_api_key_bucket_is_separate_from_ip_bucket() -> None:
    clock = FakeClock()
    resolver = HeaderKeyResolver()
    limiter = _limiter(clock, resolver=resolver)

    class _Req:
        def __init__(self) -> None:
            self.META: dict[str, str] = {"HTTP_X_API_KEY": "secret-key"}

    key_identity = f"key:{resolver.resolve(_Req())}"
    for _ in range(3):
        limiter.allow("default", key_identity)
    # The IP bucket for the same client is unaffected.
    allowed, _ = limiter.allow("default", "ip:1.1.1.1")
    assert allowed


# --------------------------------------------------------------------------- #
# Fail-open fallback
# --------------------------------------------------------------------------- #


class _BrokenStore:
    def get(self, key: str) -> Any:
        raise ConnectionError("redis down")

    def set(self, key: str, bucket: Any) -> None:
        raise ConnectionError("redis down")


def test_broken_store_degrades_to_in_process_fallback() -> None:
    clock = FakeClock()
    fallback = InProcessBucketStore()
    limiter = RateLimiter(
        store=_BrokenStore(),
        rates={"default": (1.0, 2.0)},
        fallback=fallback,
        clock=clock,
    )
    allowed, _ = limiter.allow("default", "ip:1.1.1.1")
    assert allowed
    assert limiter.degraded is True
    allowed, _ = limiter.allow("default", "ip:1.1.1.1")
    assert allowed
    allowed, _ = limiter.allow("default", "ip:1.1.1.1")
    assert not allowed  # the fallback still limits (per process)
    # The metric records the degradation.
    assert "rate_limit_fallback_total" in metrics.render()


def test_limiter_recovers_when_store_comes_back() -> None:
    class _FlakyStore:
        def __init__(self) -> None:
            self.gets = 0

        def get(self, key: str) -> Any:
            self.gets += 1
            if self.gets <= 2:
                raise ConnectionError("down")
            return None

        def set(self, key: str, bucket: Any) -> None:
            return

    clock = FakeClock()
    limiter = RateLimiter(store=_FlakyStore(), rates={"default": (1.0, 2.0)}, clock=clock)
    limiter.allow("default", "ip:1.1.1.1")  # degraded
    limiter.allow("default", "ip:1.1.1.1")  # degraded
    allowed, _ = limiter.allow("default", "ip:1.1.1.1")  # store recovered
    assert allowed
    assert limiter.degraded is False


# --------------------------------------------------------------------------- #
# DRF integration
# --------------------------------------------------------------------------- #


@pytest.fixture
def match() -> Any:
    match = make_match(status="IN_PROGRESS")
    make_match_teams(match, make_team("India"), make_team("Australia"))
    return match


@pytest.fixture(autouse=True)
def _configure_slow_limiter() -> Any:
    clock = FakeClock()

    class _ClockRateLimiter(RateLimiter):
        def __init__(self) -> None:
            super().__init__(rates={"default": (1.0, 2.0), "deliveries": (1.0, 1.0)}, clock=clock)

    configure_limiter(_ClockRateLimiter())
    yield clock
    configure_limiter(None)


def test_throttled_requests_get_429_envelope_with_retry_after(
    api_client: Any, match: Any, _configure_slow_limiter: Any
) -> None:
    url = f"/api/v1/matches/{match.id}/"
    for _ in range(2):
        assert api_client.get(url).status_code == 200
    denied = api_client.get(url)
    assert denied.status_code == 429
    body = denied.json()
    assert body["error"]["code"] == "rate_limited"
    assert int(denied["Retry-After"]) == 1


def test_cache_hits_are_throttled_too(
    api_client: Any, match: Any, _configure_slow_limiter: Any
) -> None:
    url = f"/api/v1/matches/{match.id}/"
    first = api_client.get(url)
    assert first.status_code == 200
    assert first["ETag"]  # built (miss) response
    # Second request is a cache hit but still consumes a token; the third
    # exceeds the burst of 2 even though every request is served from cache.
    second = api_client.get(url)
    assert second.status_code == 200
    assert second.content == first.content  # served from cache
    denied = api_client.get(url)
    assert denied.status_code == 429
    assert denied.json()["error"]["code"] == "rate_limited"


def test_deliveries_scope_is_stricter_than_default(
    api_client: Any, match: Any, _configure_slow_limiter: Any
) -> None:
    from apps.deliveries.services.validation import DeliveryDraft
    from apps.deliveries.services.write import append_or_revise
    from tests.phase_c.factories import make_innings, make_player, make_provider

    provider = make_provider()
    bowler = make_player("Jasprit Bumrah")
    batter = make_player("Virat Kohli")
    non_striker = make_player("Rohit Sharma")
    team1, team2 = match.teams.all()[0].team, match.teams.all()[1].team
    innings = make_innings(match, team1, team2, innings_number=1)
    append_or_revise(
        innings,
        [
            DeliveryDraft(
                delivery_seq=1,
                over_number=1,
                ball_in_over=1,
                bowler_id=bowler.id,
                batter_id=batter.id,
                non_striker_id=non_striker.id,
            )
        ],
        source=provider,
        suppress_events=True,
    )

    shell = f"/api/v1/matches/{match.id}/"
    deliveries = f"/api/v1/matches/{match.id}/innings/1/deliveries"
    assert api_client.get(deliveries).status_code == 200
    assert api_client.get(deliveries).status_code == 429  # deliveries burst = 1
    # The default scope bucket for the same client is untouched.
    assert api_client.get(shell).status_code == 200


def test_spoofed_forwarded_for_does_not_bypass_limits(
    api_client: Any, match: Any, _configure_slow_limiter: Any
) -> None:
    # TRUSTED_PROXY_HOPS=0: X-Forwarded-For is ignored, so both requests land
    # in the REMOTE_ADDR bucket even though the header claims different IPs.
    url = f"/api/v1/matches/{match.id}/"
    assert api_client.get(url, headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200
    assert api_client.get(url, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200
    denied = api_client.get(url, headers={"X-Forwarded-For": "7.7.7.7"})
    assert denied.status_code == 429


def test_api_key_header_gets_its_own_bucket(api_client: Any, match: Any) -> None:
    with override_settings(RATELIMIT_KEY_HEADER="HTTP_X_API_KEY"):
        clock = FakeClock()
        configure_limiter(
            RateLimiter(
                rates={"default": (1.0, 2.0)},
                resolver=HeaderKeyResolver(),
                clock=clock,
            )
        )
        url = f"/api/v1/matches/{match.id}/"
        # The key's bucket (burst 2) is exhausted by the keyed requests.
        assert api_client.get(url, headers={"X-Api-Key": "k-1"}).status_code == 200
        assert api_client.get(url, headers={"X-Api-Key": "k-1"}).status_code == 200
        assert api_client.get(url, headers={"X-Api-Key": "k-1"}).status_code == 429
        # An unkeyed request from the same client uses the IP bucket.
        assert api_client.get(url).status_code == 200


# --------------------------------------------------------------------------- #
# RedisBucketStore (fake client, no network)
# --------------------------------------------------------------------------- #


def test_redis_store_round_trips_buckets() -> None:
    class _FakeClient:
        def __init__(self) -> None:
            self.data: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.data.get(key)

        def set(self, key: str, value: str, ex: int) -> None:
            self.data[key] = value

    fake = _FakeClient()
    store = RedisBucketStore(fake)
    store.set("default:ip:1.1.1.1", TokenBucket.fresh(1.0, 5.0, now=100.0))
    bucket = store.get("default:ip:1.1.1.1")
    assert bucket is not None
    assert bucket.tokens == 5.0
    assert bucket.rate == 1.0
    assert store.get("missing") is None


# --------------------------------------------------------------------------- #
# ConnectionLimiter (SSE/WS connect caps)
# --------------------------------------------------------------------------- #


def test_connection_limiter_caps_and_releases() -> None:
    limiter = ConnectionLimiter()
    assert limiter.try_acquire("ip:1.1.1.1", limit=2)
    assert limiter.try_acquire("ip:1.1.1.1", limit=2)
    assert not limiter.try_acquire("ip:1.1.1.1", limit=2)
    assert limiter.count("ip:1.1.1.1") == 2
    limiter.release("ip:1.1.1.1")
    assert limiter.try_acquire("ip:1.1.1.1", limit=2)
    limiter.release("ip:1.1.1.1")
    limiter.release("ip:1.1.1.1")
    assert limiter.count("ip:1.1.1.1") == 0


def test_connection_limiter_keys_are_isolated() -> None:
    limiter = ConnectionLimiter()
    assert limiter.try_acquire("match:m1", limit=1)
    assert not limiter.try_acquire("match:m1", limit=1)
    assert limiter.try_acquire("match:m2", limit=1)

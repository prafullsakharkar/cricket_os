"""Phase E group 2: Redis-backed cache layer (keys, TTLs, single-flight, invalidation).

Covers the acceptance criteria for cached reads:

* deterministic, injection-safe key construction with normalized params;
* state-dependent TTL policy (live / completed / immutable after the
  reconciliation window) expressed as HTTP cache directives;
* key-versioned invalidation (bump a version, every derived key is dead);
* single-flight so N concurrent misses cause one build;
* stale-while-revalidate and a local breaker that degrades to a direct build
  when Redis is unavailable;
* a complete event-topic -> cache-namespace registry and a surrogate-key
  purger (local and HTTP/CDN, no network in tests).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from apps.core import events
from apps.core.cache import invalidation, keys, policy, purger, store, versions

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_store_state() -> Any:
    """The breaker and refresh registry are process-global; reset per test."""
    store.reset_breaker()
    yield
    store.reset_breaker()


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #
def test_make_key_is_stable_for_equal_inputs() -> None:
    a = keys.make_key("match:scorecard", "m1", params={"innings": 1, "fields": "runs"})
    b = keys.make_key("match:scorecard", "m1", params={"fields": "runs", "innings": 1})
    assert a.base == b.base
    assert a.render(version=3) == b.render(version=3)


def test_make_key_drops_none_and_normalizes_scalars() -> None:
    key = keys.make_key(
        "players",
        params={"q": None, "page": 1, "active": True, "sort": "name", "empty": ""},
    )
    assert "q=" not in key.base
    assert "active=1" in key.base
    assert "page=1" in key.base
    assert "empty=" in key.base


def test_make_key_orders_and_joins_sequences() -> None:
    key = keys.make_key("matches", params={"team": ["a", "b"], "format": "T20"})
    assert "team=a,b" in key.base
    assert key.base.index("format=") < key.base.index("team=")


def test_make_key_prevents_part_collisions() -> None:
    # A ':' inside a part must not make two different resources collide.
    assert keys.make_key("x", "a:b").base != keys.make_key("x", "a", "b").base
    assert keys.make_key("x", "a/b").base != keys.make_key("x", "a", "b").base


def test_make_key_render_includes_version_and_namespace() -> None:
    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    rendered = key.render(version=7)
    assert rendered.startswith("cache:match:shell:v7:")
    assert "m1" in rendered
    assert key.version_ns == "match:m1"


def test_surrogate_keys_are_derived_from_namespaces() -> None:
    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    assert key.surrogates == ("match-m1",)


# --------------------------------------------------------------------------- #
# TTL policy
# --------------------------------------------------------------------------- #
def test_live_plan_has_seconds_ttl_and_swr() -> None:
    plan = policy.for_live()
    assert plan.redis_ttl <= 5
    assert plan.cache_control() == "public, max-age=5, stale-while-revalidate=5"


def test_completed_plan_has_long_cdn_ttl_and_stale_if_error() -> None:
    plan = policy.for_completed()
    control = plan.cache_control()
    assert "max-age=300" in control
    assert "s-maxage=3600" in control
    assert "stale-while-revalidate=600" in control
    assert "stale-if-error=86400" in control
    assert "immutable" not in control


def test_immutable_plan_is_immutable_for_a_year() -> None:
    control = policy.for_immutable().cache_control()
    assert "max-age=31536000" in control
    assert "s-maxage=31536000" in control
    assert "immutable" in control


def test_terminal_match_switches_to_immutable_after_reconciliation_window() -> None:
    now = 1_800_000_000.0
    window = 24 * 3600
    recent = policy.for_match("COMPLETED", ended_at=now - window + 1, now=now)
    old = policy.for_match("COMPLETED", ended_at=now - window - 1, now=now)
    assert "s-maxage=3600" in recent.cache_control()
    assert "immutable" in old.cache_control()


def test_live_match_uses_live_plan_even_with_an_end_time() -> None:
    plan = policy.for_match("IN_PROGRESS", ended_at=1, now=1_800_000_000.0)
    assert plan.cache_control() == policy.for_live().cache_control()


def test_plan_can_be_adapted_for_reference_data() -> None:
    reference = policy.for_reference().cache_control()
    fixtures = policy.for_fixtures().cache_control()
    assert reference == "public, max-age=600"
    assert fixtures == "public, max-age=30, stale-while-revalidate=60"


# --------------------------------------------------------------------------- #
# Version store
# --------------------------------------------------------------------------- #
def test_version_defaults_to_zero_and_bumps() -> None:
    assert versions.current("match:abc") == 0
    assert versions.bump("match:abc") == 1
    assert versions.current("match:abc") == 1
    assert versions.bump("match:abc") == 3 - 1


def test_bump_many_bumps_each_namespace_once() -> None:
    versions.bump_many(["a", "b", "a"])
    assert versions.current("a") == 1
    assert versions.current("b") == 1


# --------------------------------------------------------------------------- #
# Store: hit/miss, single-flight, SWR, degradation
# --------------------------------------------------------------------------- #
def _plan(ttl: int = 5, swr: int = 5) -> policy.CachePlan:
    return policy.CachePlan(redis_ttl=ttl, max_age=ttl, swr=swr, s_maxage=None, stale_if_error=None)


def test_get_or_build_miss_then_hit() -> None:
    calls = {"n": 0}

    def build() -> dict[str, int]:
        calls["n"] += 1
        return {"runs": 10}

    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    first = store.get_or_build(key, build, plan=_plan())
    second = store.get_or_build(key, build, plan=_plan())
    assert first.value == {"runs": 10}
    assert first.hit is False
    assert second.value == {"runs": 10}
    assert second.hit is True
    assert calls["n"] == 1


def test_bump_versions_invalidates_without_deleting() -> None:
    calls = {"n": 0}

    def build() -> int:
        calls["n"] += 1
        return calls["n"]

    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    assert store.get_or_build(key, build, plan=_plan()).value == 1
    versions.bump("match:m1")
    assert store.get_or_build(key, build, plan=_plan()).value == 2


def test_single_flight_builds_once_for_concurrent_misses() -> None:
    calls = {"n": 0}

    def build() -> dict[str, int]:
        calls["n"] += 1
        time.sleep(0.05)
        return {"n": calls["n"]}

    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    results: list[Any] = []
    import threading

    def worker() -> None:
        results.append(store.get_or_build(key, build, plan=_plan()))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls["n"] == 1
    assert len(results) == 6
    assert {r.value["n"] for r in results} == {1}
    assert all(r.value == {"n": 1} for r in results)


def test_stale_entry_is_served_and_revalidated() -> None:
    now = {"t": 1000.0}
    calls = {"n": 0}

    def build() -> int:
        calls["n"] += 1
        return calls["n"]

    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    plan = _plan(ttl=10, swr=10)
    first = store.get_or_build(key, build, plan=plan, clock=lambda: now["t"])
    assert first.value == 1

    now["t"] += 11  # past soft expiry, inside the hard (swr) window
    stale = store.get_or_build(
        key, build, plan=plan, clock=lambda: now["t"], scheduler=lambda fn: fn()
    )
    assert stale.value == 1
    assert stale.stale is True
    assert calls["n"] == 2

    fresh = store.get_or_build(key, build, plan=plan, clock=lambda: now["t"])
    assert fresh.value == 2
    assert fresh.stale is False


def test_expired_entry_rebuilds_when_swr_is_disabled() -> None:
    now = {"t": 0.0}
    calls = {"n": 0}

    def build() -> int:
        calls["n"] += 1
        return calls["n"]

    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    plan = _plan(ttl=5, swr=0)
    store.get_or_build(key, build, plan=plan, clock=lambda: now["t"])
    now["t"] += 6
    result = store.get_or_build(key, build, plan=plan, clock=lambda: now["t"])
    assert result.value == 2
    assert result.hit is False


def test_breaker_degrades_to_direct_build_on_cache_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenCache:
        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("redis down")

        def set(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("redis down")

    monkeypatch.setattr(store, "cache_backend", lambda: BrokenCache())
    store.reset_breaker()
    calls = {"n": 0}

    def build() -> int:
        calls["n"] += 1
        return calls["n"]

    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    for _ in range(4):
        assert store.get_or_build(key, build, plan=_plan()).value == calls["n"]
    assert calls["n"] == 4
    assert store.breaker.is_open() is True


def test_breaker_closes_after_recovery_window() -> None:
    now = {"t": 0.0}
    breaker = store.LocalBreaker(failure_threshold=2, recovery_seconds=10, clock=lambda: now["t"])
    breaker.record_failure()
    assert breaker.is_open() is False
    breaker.record_failure()
    assert breaker.is_open() is True
    assert breaker.allow_request() is False
    now["t"] += 11
    assert breaker.allow_request() is True  # half-open probe
    breaker.record_success()
    assert breaker.is_open() is False


# --------------------------------------------------------------------------- #
# Invalidation registry
# --------------------------------------------------------------------------- #
_MATCH_PAYLOAD = {
    "match_id": "11111111-1111-4111-8111-111111111111",
    "innings_id": "22222222-2222-4222-8222-222222222222",
}
_TOPICS: list[tuple[str, dict[str, Any]]] = [
    (events.TOPIC_DELIVERY_APPENDED, _MATCH_PAYLOAD),
    (events.TOPIC_DELIVERY_CORRECTED, _MATCH_PAYLOAD),
    (events.TOPIC_SCORECARD_UPDATED, _MATCH_PAYLOAD),
    (events.TOPIC_MATCH_STATE_CHANGED, _MATCH_PAYLOAD),
    (events.TOPIC_MATCH_COMPLETED, _MATCH_PAYLOAD),
    (events.TOPIC_CORRECTION_APPLIED, _MATCH_PAYLOAD),
    (events.TOPIC_MATCH_REOPENED, _MATCH_PAYLOAD),
    (events.TOPIC_PLAYER_MERGED, {"canonical_player_id": "p1", "merged_player_id": "p2"}),
    (events.TOPIC_PLAYER_UPDATED, {"player_id": "p1"}),
    (events.TOPIC_TEAM_UPDATED, {"team_id": "t1"}),
    (events.TOPIC_VENUE_UPDATED, {"venue_id": "v1"}),
    (events.TOPIC_SERIES_UPDATED, {"series_id": "s1"}),
    (events.TOPIC_COMPETITION_UPDATED, {"competition_id": "c1"}),
]


@pytest.mark.parametrize(("topic", "payload"), _TOPICS)
def test_registry_covers_every_change_topic(topic: str, payload: dict[str, Any]) -> None:
    invalidation.register_defaults()
    assert invalidation.namespaces_for(topic, payload), f"{topic} has no cache invalidation"


def test_delivery_appended_invalidates_match_and_live_feed() -> None:
    namespaces = invalidation.namespaces_for(events.TOPIC_DELIVERY_APPENDED, _MATCH_PAYLOAD)
    assert f"match:{_MATCH_PAYLOAD['match_id']}" in namespaces
    assert "matches:live" in namespaces


def test_match_completed_invalidates_recent_and_match() -> None:
    namespaces = invalidation.namespaces_for(events.TOPIC_MATCH_COMPLETED, _MATCH_PAYLOAD)
    assert f"match:{_MATCH_PAYLOAD['match_id']}" in namespaces
    assert "matches:recent" in namespaces


def test_player_merged_invalidates_both_players_and_the_list() -> None:
    namespaces = invalidation.namespaces_for(
        events.TOPIC_PLAYER_MERGED,
        {"canonical_player_id": "p1", "merged_player_id": "p2"},
    )
    assert {"player:p1", "player:p2", "players"} <= namespaces


def test_unknown_topic_is_a_noop() -> None:
    assert invalidation.namespaces_for("nope", {"match_id": "m"}) == set()


def test_invalidation_never_raises_when_the_cache_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(namespaces: Any) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(invalidation, "_bump", boom)
    invalidation.invalidate_event(events.TOPIC_DELIVERY_APPENDED, _MATCH_PAYLOAD)


def test_local_purger_bumps_versions() -> None:
    purger.LocalCachePurger().purge(["match:m1", "matches:live"])
    assert versions.current("match:m1") == 1
    assert versions.current("matches:live") == 1


def test_http_purger_sends_surrogate_keys_through_the_injected_transport() -> None:
    calls: list[dict[str, Any]] = []

    def transport(method: str, url: str, *, headers: dict[str, str], timeout: float) -> Any:
        calls.append({"method": method, "url": url, "headers": headers, "timeout": timeout})
        return type("Response", (), {"status_code": 204})()

    http = purger.HttpCachePurger("https://cdn.example/purge", transport=transport)
    http.purge(["match-m1", "matches-live"])
    assert calls == [
        {
            "method": "PURGE",
            "url": "https://cdn.example/purge",
            "headers": {"Surrogate-Key": "match-m1 matches-live"},
            "timeout": 2.0,
        }
    ]


def test_purger_factory_defaults_to_local_without_a_cdn_url() -> None:
    assert isinstance(purger.for_settings(cdn_url=""), purger.LocalCachePurger)
    assert isinstance(
        purger.for_settings(cdn_url="https://cdn.example/purge"), purger.HttpCachePurger
    )


def test_get_or_build_can_capture_the_event_sequence() -> None:
    key = keys.make_key("match:shell", "m1", version_ns="match:m1")
    result = store.get_or_build(
        key,
        lambda: {"runs": 1},
        plan=_plan(),
        event_seq=lambda: 42,
    )
    assert result.event_seq == 42
    again = store.get_or_build(key, lambda: {"runs": 1}, plan=_plan(), event_seq=lambda: 99)
    assert again.event_seq == 42  # captured with the cached snapshot

#!/usr/bin/env python3
"""Phase E load smoke gate (acceptance #1/#10): cached reads + realtime fan-out.

Usage:
    uv run python loadtests/smoke_gate.py [--skip-migrate]

Runs under the test settings (``config.settings.test``) against the database
from ``DATABASE_URL``, which the script migrates itself (idempotent — a no-op
against an already-migrated local DB, the schema bootstrap for a fresh CI DB).
Two budgets from acceptance #10 (ledger):

1. **Cached reads** — seed live matches, warm the read-through cache with one
   request, then measure warm ``GET /api/v1/matches/live`` responses through
   ``django.test.Client()`` (the full stack: middleware, token-bucket
   throttle, cache hit, DRF rendering). Gate: p95 ≤ 200 ms.

2. **Realtime fan-out** — a real :class:`apps.realtime.hub.Hub` with the real
   coalesce ticker (``flush_interval`` 0.2 s), 100 subscribers on one match, a
   1000-frame burst coalesced into batch frames, and an immediate-correction
   flush (which never waits for the tick, ledger #6). Gate: publish →
   last-subscriber delivery ≤ 1 s.

The script seeds and cleans up its own rows, prints a p50/p95/p99/max report,
and exits non-zero on any budget breach. ``loadtests/README.md`` documents the
manual k6 scripts for capacity runs against a deployed node.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

# -- budgets (acceptance #10) ------------------------------------------------- #

CACHED_READ_P95_BUDGET_S = 0.200
FANOUT_BUDGET_S = 1.0

# -- scenario sizes ------------------------------------------------------------ #

LIVE_MATCH_COUNT = 20
WARM_READS = 100
FANOUT_SUBSCRIBERS = 100
BURST_FRAMES = 1000
FANOUT_FLUSH_INTERVAL_S = 0.2

#: Hard ceiling for the fan-out wait loop; a breach fails fast, never hangs.
WAIT_TIMEOUT_S = 5.0


def _pctile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def _report(name: str, samples: list[float]) -> None:
    if not samples:
        print(f"{name:<32} (no samples)")
        return
    print(
        f"{name:<32} n={len(samples):>4}  p50={_pctile(samples, 50) * 1e3:8.2f}ms  "
        f"p95={_pctile(samples, 95) * 1e3:8.2f}ms  p99={_pctile(samples, 99) * 1e3:8.2f}ms  "
        f"max={max(samples) * 1e3:8.2f}ms"
    )


# --------------------------------------------------------------------------- #
# Part 1 — cached reads
# --------------------------------------------------------------------------- #


def _clean_stale_seeds() -> None:
    """Delete leftover seed rows from a previously interrupted run.

    Every row the gate creates carries the ``Smoke `` team prefix, so a crash
    (or a Ctrl-C) never wedges a re-run. Delete in dependency order:
    MatchTeam protects Match, MatchTeam protects Team.
    """
    from apps.matches.models import Match, MatchTeam
    from apps.teams.models import Team

    stale_matches = Match.objects.filter(teams__team__name__startswith="Smoke ").distinct()
    MatchTeam.objects.filter(match_id__in=stale_matches.values("id")).delete()
    stale_matches.delete()
    Team.objects.filter(name__startswith="Smoke ").delete()


def bench_cached_reads() -> dict[str, float]:
    """Seed live matches, warm the cache, measure warm reads (p95 ≤ 200 ms)."""
    from datetime import timedelta

    from django.test import Client
    from django.utils import timezone
    from tests.phase_c.factories import make_match, make_match_teams, make_team

    from apps.core.enums import MatchStatus

    _clean_stale_seeds()
    client = Client()
    seeded_matches: list[Any] = []
    seeded_teams: list[Any] = []
    now = timezone.now()
    for i in range(LIVE_MATCH_COUNT):
        # Newest-start first ordering (the feed sorts by -start_time_utc), so
        # these are always on the first page and never crowd real data out.
        match = make_match(
            format_type="T20I",
            overs_limit=20,
            status=MatchStatus.IN_PROGRESS,
            start_time_utc=now + timedelta(seconds=i),
        )
        team1 = make_team(f"Smoke Alpha {i}")
        team2 = make_team(f"Smoke Beta {i}")
        make_match_teams(match, team1, team2)
        seeded_matches.append(match)
        seeded_teams.extend((team1, team2))

    try:
        # Cold build: one request populates the read-through cache.
        started = time.perf_counter()
        cold = client.get("/api/v1/matches/live")
        cold_ms = (time.perf_counter() - started) * 1e3
        assert cold.status_code == 200, f"cold read returned {cold.status_code}"
        payload = cold.json()
        items = payload["data"]
        returned = {str(item["id"]) for item in items}
        assert returned >= {str(match.id) for match in seeded_matches}, (
            "seeded live matches missing from the feed"
        )

        samples: list[float] = []
        for _ in range(WARM_READS):
            started = time.perf_counter()
            warm = client.get("/api/v1/matches/live")
            samples.append(time.perf_counter() - started)
            assert warm.status_code == 200, f"warm read returned {warm.status_code}"

        p50 = _pctile(samples, 50)
        p95 = _pctile(samples, 95)
        p99 = _pctile(samples, 99)
        print(f"{'cold build':<32} 1 request  {cold_ms:8.2f}ms")
        _report("cached reads (warm)", samples)
        print(
            f"  budget: p95 <= {CACHED_READ_P95_BUDGET_S * 1e3:.0f} ms "
            f"-> {'PASS' if p95 <= CACHED_READ_P95_BUDGET_S else 'FAIL'}"
        )
        return {"cold_ms": cold_ms, "p50": p50, "p95": p95, "p99": p99}
    finally:
        _clean_stale_seeds()


# --------------------------------------------------------------------------- #
# Part 2 — realtime fan-out
# --------------------------------------------------------------------------- #


class _NoopReader:
    """Reader stand-in: the burst is contiguous so no gap backfill fires."""

    def events_since(self, match_id: str, last_seq: int) -> Any:
        from apps.core.streaming import StreamInfo

        return StreamInfo()

    def latest_seq(self, match_id: str) -> int:
        return 0


async def _wait_for_seq(subs: list[Any], target_seq: int) -> float:
    """Return seconds until every subscriber has seen ``target_seq``."""
    started = time.perf_counter()
    while time.perf_counter() - started < WAIT_TIMEOUT_S:
        if all(sub.last_seq >= target_seq for sub in subs):
            return time.perf_counter() - started
        await asyncio.sleep(0.002)
    reached = sum(1 for sub in subs if sub.last_seq >= target_seq)
    raise RuntimeError(
        f"fan-out timeout: {reached}/{len(subs)} subscribers reached seq {target_seq}"
    )


def bench_fanout() -> dict[str, float]:
    """100 subscribers, 1000-frame burst + immediate correction (≤ 1 s)."""
    from apps.realtime.frames import Frame
    from apps.realtime.hub import Hub

    async def scenario() -> tuple[float, float]:
        match_id = "smoke-fanout"
        hub = Hub(reader=_NoopReader(), flush_interval=FANOUT_FLUSH_INTERVAL_S)
        hub.start()  # the real coalesce ticker
        subs: list[Any] = []
        try:
            for _ in range(FANOUT_SUBSCRIBERS):
                subs.append(await hub.subscribe(match_id))
            assert hub.subscriber_count() == FANOUT_SUBSCRIBERS

            # Burst of ordinary deltas: coalesced into batch frames by the ticker.
            started = time.perf_counter()
            for seq in range(1, BURST_FRAMES + 1):
                await hub.publish(
                    Frame(
                        seq=seq,
                        event_type="delivery.appended",
                        match_id=match_id,
                        payload={"delivery_seq": seq},
                    )
                )
            burst_s = await _wait_for_seq(subs, BURST_FRAMES)
            burst_s = max(burst_s, time.perf_counter() - started)

            # Correction flush: immediate, never waits for the tick (ledger #6).
            started = time.perf_counter()
            await hub.publish(
                Frame(
                    seq=BURST_FRAMES + 1,
                    event_type="correction.applied",
                    match_id=match_id,
                    payload={},
                )
            )
            correction_s = await _wait_for_seq(subs, BURST_FRAMES + 1)
            correction_s = max(correction_s, time.perf_counter() - started)

            for sub in subs:
                hub.unsubscribe(sub)
            return burst_s, correction_s
        finally:
            await hub.stop()

    burst_s, correction_s = asyncio.run(scenario())
    print(f"{'fan-out burst (1000 frames)':<32} {burst_s * 1e3:8.2f}ms")
    print(f"{'fan-out correction (immediate)':<32} {correction_s * 1e3:8.2f}ms")
    worst = max(burst_s, correction_s)
    print(
        f"  budget: <= {FANOUT_BUDGET_S * 1e3:.0f} ms "
        f"-> {'PASS' if worst <= FANOUT_BUDGET_S else 'FAIL'}"
    )
    return {"burst": burst_s, "correction": correction_s, "worst": worst}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase E load smoke gate")
    parser.add_argument(
        "--skip-migrate",
        action="store_true",
        help="skip the idempotent migrate (already migrated database)",
    )
    args = parser.parse_args(argv)

    import django

    django.setup()

    if not args.skip_migrate:
        from django.core.management import call_command

        call_command("migrate", interactive=False, verbosity=0)

    cached = bench_cached_reads()
    fanout = bench_fanout()

    ok = cached["p95"] <= CACHED_READ_P95_BUDGET_S and fanout["worst"] <= FANOUT_BUDGET_S
    print(
        "\nload smoke gate: "
        + ("PASS" if ok else "FAIL")
        + (
            ""
            if ok
            else f" (cached p95 {cached['p95'] * 1e3:.1f}ms, "
            f"fan-out worst {fanout['worst'] * 1e3:.1f}ms)"
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

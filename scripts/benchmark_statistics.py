#!/usr/bin/env python3
"""Benchmark the Phase F statistics surface (report-only, not in make check).

Two measurements feed the Phase F final report:

1. **Tier-2 backfill** — seed ``--matches`` completed matches (Tier-1
   ``player_match_stat`` rows present, Tier-2 empty), then time
   ``rebuild_statistics all``. Reports wall time, matches/s, and an
   extrapolation to larger corpora (a linear-scaled estimate — real data grows
   sub-linearly because recompute_keys deduplicates by aggregate key).

2. **Uncached p95 read latency** — after the backfill, measure build-on-miss
   latency through ``django.test.Client`` (full stack: middleware, token-bucket
   throttle, DRF rendering) for the leaderboard, points-table, player-statistics
   and search endpoints. The cache is cleared before every sample, so each
   measurement is a cold build (the number the brief asks for).

Usage:
    DATABASE_URL=postgres://.../cricket_bench \
        uv run python scripts/benchmark_statistics.py [--matches 1000] [--samples 50]

The target database must already be migrated. Point ``DATABASE_URL`` at a
scratch DB — the script seeds and cleans up its own rows (``Bench `` prefixes).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

#: Scenario sizes.
DEFAULT_MATCHES = 1_000
DEFAULT_SAMPLES = 50
PLAYER_POOL = 16
TEAM_POOL = 8
#: Matches per series. Backfill cost is dominated by *series* standings scans:
#: ``rebuild_statistics`` recomputes the standings key per match, and each scan
#: touches every match in that series — a 1 000-match single series is O(n²).
#: Real corpora spread matches across series, so the benchmark does too (the
#: single-series worst case is discussed in the final report, not measured).
MATCHES_PER_SERIES = 20

#: Extrapolation targets (linear estimate, matches/s × dataset size).
EXTRAPOLATE_TO = (10_000, 100_000)


def _pctile(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def _report(name: str, samples: list[float]) -> None:
    if not samples:
        print(f"{name:<34} (no samples)")
        return
    print(
        f"{name:<34} n={len(samples):>4}  p50={_pctile(samples, 50) * 1e3:8.2f}ms  "
        f"p95={_pctile(samples, 95) * 1e3:8.2f}ms  p99={_pctile(samples, 99) * 1e3:8.2f}ms  "
        f"max={max(samples) * 1e3:8.2f}ms"
    )


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def _clean_stale_seeds() -> None:
    """Delete leftover benchmark rows from an interrupted run (dependency order)."""
    from apps.competitions.models import Series
    from apps.matches.models import Match, MatchResult, MatchTeam
    from apps.players.models import Player
    from apps.statistics.models import (
        FormTrend,
        HeadToHead,
        Leaderboard,
        PlayerCareerStat,
        PlayerMatchStat,
        StatisticsWatermark,
        TeamStat,
        TournamentStandings,
    )
    from apps.teams.models import Team

    matches = Match.objects.filter(series__name__startswith="Bench ").distinct()
    player_ids = list(
        Player.objects.filter(full_name__startswith="Bench ").values_list("id", flat=True)
    )
    team_ids = list(Team.objects.filter(name__startswith="Bench ").values_list("id", flat=True))
    series_ids = list(Series.objects.filter(name__startswith="Bench ").values_list("id", flat=True))
    if matches:
        MatchTeam.objects.filter(match_id__in=matches.values("id")).delete()
        PlayerMatchStat.objects.filter(match_id__in=matches.values("id")).delete()
        StatisticsWatermark.objects.filter(match_id__in=matches.values("id")).delete()
        MatchResult.objects.filter(match_id__in=matches.values("id")).delete()
    if player_ids:
        FormTrend.objects.filter(player_id__in=player_ids).delete()
        Leaderboard.objects.filter(player_id__in=player_ids).delete()
        PlayerCareerStat.objects.filter(player_id__in=player_ids).delete()
    if team_ids:
        TeamStat.objects.filter(team_id__in=team_ids).delete()
        Leaderboard.objects.filter(team_id__in=team_ids).delete()
        HeadToHead.objects.filter(team_low_id__in=team_ids).delete()
        HeadToHead.objects.filter(team_high_id__in=team_ids).delete()
    if series_ids:
        TournamentStandings.objects.filter(series_id__in=series_ids).delete()
    if matches:
        matches.delete()
    if team_ids:
        Team.objects.filter(id__in=team_ids).delete()
    if player_ids:
        Player.objects.filter(id__in=player_ids).delete()
    if series_ids:
        Series.objects.filter(id__in=series_ids).delete()


def _seed(match_count: int) -> dict[str, Any]:
    """Seed ``match_count`` completed matches across series + Tier-1 rows."""
    from tests.phase_c.factories import make_match, make_match_teams, make_player, make_team
    from tests.phase_f.test_recompute import make_stat

    from apps.competitions.models import Series
    from apps.matches.models import MatchResult

    _clean_stale_seeds()
    # Scale the entity pools with the corpus so the per-match backfill cost
    # stays bounded (teams/players span a realistic share of matches, not the
    # whole corpus — a fixed tiny pool makes backfill O(n²) in match count).
    team_pool = max(TEAM_POOL, match_count // 25)
    player_pool = max(PLAYER_POOL, match_count // 8)
    teams = [make_team(f"Bench Team {i}") for i in range(team_pool)]
    players = [make_player(f"Bench Player {i}") for i in range(player_pool)]

    matches: list[Any] = []
    series_count = max(1, -(-match_count // MATCHES_PER_SERIES))
    for s in range(series_count):
        series = Series.objects.create(name=f"Bench Series {s}", is_tournament=True)
        for i in range(min(MATCHES_PER_SERIES, match_count - len(matches))):
            t1 = teams[i % TEAM_POOL]
            t2 = teams[(i + 1) % TEAM_POOL]
            match = make_match(
                format_type="T20I",
                gender="M",
                status="COMPLETED",
                series=series,
            )
            make_match_teams(match, t1, t2)
            winner = t1 if i % 2 == 0 else t2
            MatchResult.objects.create(match=match, result_type="WIN", winner_team=winner)
            # Two batters per side with deterministic, varied lines.
            p1, p2 = players[i % PLAYER_POOL], players[(i + 3) % PLAYER_POOL]
            p3, p4 = players[(i + 5) % PLAYER_POOL], players[(i + 7) % PLAYER_POOL]
            make_stat(
                match,
                p1,
                t1,
                runs=(i % 90) + 10,
                balls=40,
                out=True,
                wickets=i % 3,
                balls_bowled=24,
                runs_conceded=(i % 40) + 10,
            )
            make_stat(match, p2, t1, runs=(i % 70) + 5, balls=35, out=False)
            make_stat(
                match,
                p3,
                t2,
                runs=(i % 60) + 15,
                balls=30,
                out=True,
                wickets=(i + 1) % 3,
                balls_bowled=18,
                runs_conceded=(i % 35) + 8,
            )
            make_stat(match, p4, t2, runs=(i % 50) + 1, balls=25, out=False)
            matches.append(match)
    return {"series": matches[0].series, "players": players, "teams": teams, "matches": matches}


def _cleanup(seed: dict[str, Any]) -> None:
    _clean_stale_seeds()


# --------------------------------------------------------------------------- #
# Backfill + latency
# --------------------------------------------------------------------------- #


def bench_backfill(seed: dict[str, Any]) -> dict[str, float]:
    from apps.statistics.services.recompute import rebuild_statistics

    started = time.perf_counter()
    count = rebuild_statistics("all")
    elapsed = time.perf_counter() - started
    rate = count / elapsed if elapsed else 0.0
    print(f"\nTier-2 backfill: {count} matches in {elapsed:.2f}s ({rate:.0f} matches/s)")
    for target in EXTRAPOLATE_TO:
        print(f"  linear extrapolation to {target:>7,} matches: ~{target / rate / 60:.1f} min")
    return {"matches": count, "seconds": elapsed}


def bench_latency(seed: dict[str, Any]) -> None:
    import time as _time

    from django.core.cache import caches
    from django.test import Client

    client = Client()
    series = seed["series"]
    player_id = seed["players"][0].id
    endpoints = {
        "leaderboard /statistics/batting": "/api/v1/statistics/batting?scope=CAREER",
        "points-table /series/{id}/points-table": f"/api/v1/series/{series.id}/points-table",
        "player-stats /players/{id}/statistics": f"/api/v1/players/{player_id}/statistics",
        "search /search?q=Bench+Player+1": "/api/v1/search?q=Bench%20Player%201",
    }
    print()
    for label, url in endpoints.items():
        samples: list[float] = []
        for _ in range(args.samples):
            # Clear *both* aliases — the read-through cache lives on ``cache``.
            caches["default"].clear()
            caches["cache"].clear()
            started = _time.perf_counter()
            response = client.get(url)
            samples.append(_time.perf_counter() - started)
            assert response.status_code == 200, f"{label} -> {response.status_code}"
            # Stay inside the token bucket (10/s refill, 120 burst): spacing the
            # samples lets the bucket refill between requests.
            _time.sleep(0.1)
        _report(label, samples)


def main() -> None:
    seed = _seed(args.matches)
    try:
        bench_backfill(seed)
        bench_latency(seed)
    finally:
        _cleanup(seed)
        print("\ncleaned up benchmark rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches", type=int, default=DEFAULT_MATCHES)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    args = parser.parse_args()

    import django

    django.setup()
    main()

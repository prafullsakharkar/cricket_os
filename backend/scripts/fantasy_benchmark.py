#!/usr/bin/env python3
"""Benchmark the Phase H fantasy surface (report-only, not in make check).

Four measurements feed the Phase H final report (plan Task 10 step 3):

1. **Compute time per match (p95)** — the pipeline freezes the engine timing on
   the append-only ``fantasy_calc_audit.timings`` jsonb at write
   (``compute_ms``). After a full backfill, the p50/p95/p99/max of that
   distribution *is* the compute-per-match number — measured per run, not
   wall-clock averaged.

2. **Tier-2 backfill** — seed ``--matches`` completed matches (canonical
   ``player_match_stat`` rows present, fantasy tables empty), then time
   ``backfill_matches("all")``. Reports wall time, matches/s, and a linear
   extrapolation to larger corpora (a linear-scaled estimate; the pipeline's
   idempotency no-op makes re-runs cheaper than the first).

3. **Endpoint p95 uncached/cached** — measure the three public fantasy
   endpoints through ``django.test.Client`` (the full stack: middleware,
   token-bucket throttle, read-through cache, DRF rendering). Uncached samples
   clear the cache first (cold build); cached samples warm once, then sample
   the cache-hit path.

4. **EXPLAIN gates** — the read path must resolve ``player_match_performance``
   through the ``ux_pmp`` (match_id, player_id, ruleset_version_id) index — no
   sequential scan on the largest fantasy table. The latest-calc-run lookup
   (``fantasy_calc_audit`` by match_id) has no index in the §3.8 DDL; the plan
   is reported as an observation, not a gate, at this corpus size.

Usage:
    DATABASE_URL=postgres://.../cricket_bench \
        uv run python scripts/fantasy_benchmark.py [--matches 1000] [--samples 50] [--reset]

The target database must already be migrated. Point ``DATABASE_URL`` at a
scratch DB — the script seeds ``Bench ``-prefixed rows and cleans up the
deletable ones. The append-only ledger/adjustments/calc-audits and the frozen
ruleset versions are immutable by design (their DB triggers forbid DELETE for
every role), so a re-run against the same DB must pass ``--reset``, which
drops and re-applies the fantasy migrations.
"""

from __future__ import annotations

import argparse
import os
import sys
import time as _time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

from apps.fantasy.engine import point_rule  # noqa: E402  (after sys.path + settings)

DEFAULT_MATCHES = 1_000
DEFAULT_SAMPLES = 50

#: Entity-pool floors. Like the statistics benchmark, pools scale with the
#: corpus so per-match cost stays bounded (a fixed tiny pool makes the backfill
#: O(n²) in match count once the snapshot/audit lookups grow).
TEAM_POOL = 8
PLAYER_POOL = 16
#: Matches per series — the fantasy backfill is per-match (no series-wide
#: standings scans), so the series layout only affects seed cost.
MATCHES_PER_SERIES = 20

#: Extrapolation targets (linear estimate, matches/s × dataset size).
EXTRAPOLATE_TO = (10_000, 100_000)

#: A realistic, fully-supported ruleset (every metric computable from the
#: summary stat lines the seed writes — no ball-by-ball / fielding-credit
#: needs, so the compute-per-match number is not skewed by unsupported rules).
RULES = (
    point_rule("playing_xi", "flag", "4.00", "participation"),
    point_rule("runs", "per_unit", "1.00", "batting"),
    point_rule("fours", "per_unit", "1.00", "batting"),
    point_rule("sixes", "per_unit", "2.00", "batting"),
    point_rule("duck", "flag", "-5.00", "batting"),
    point_rule("run_milestone", "threshold_bonus", "10.00", "batting", min_value=50),
    point_rule("wicket", "per_unit", "25.00", "bowling"),
    point_rule(
        "wicket_haul",
        "tiered",
        "0.00",
        "bowling",
        tiers=[(4, "20.00"), (5, "50.00")],
        tier_mode="highest_only",
    ),
    point_rule("player_of_match", "flag", "50.00", "participation"),
)


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


def _admin() -> Any:
    from django.contrib.auth.models import Group, User

    user, _ = User.objects.get_or_create(username="bench-admin")
    user.groups.add(Group.objects.get(name="admin"))
    return user


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def _reset_fantasy_tables() -> None:
    """Drop + re-apply the fantasy migrations (removes stale bench rows and the
    append-only/frozen triggers, then recreates everything).

    The ledger / adjustments / calc audits and frozen versions are immutable by
    design — DELETE raises for every role including the table owner — so the
    only way to scrub a re-run's fantasy rows is to reverse the migrations.
    """
    from django.core.management import call_command

    call_command("migrate", "fantasy", "zero", interactive=False, verbosity=0)
    call_command("migrate", "fantasy", interactive=False, verbosity=0)
    print("  reset fantasy tables (dropped + re-applied migrations)")


def _leftover_fantasy_rows() -> int:
    """Bench fantasy rows left by an earlier run (rulesets/versions are frozen
    and the audits/ledger append-only, so they are NOT deletable)."""
    from apps.fantasy.models import FantasyCalcAudit, FantasyRuleset
    from apps.matches.models import Match

    rulesets = FantasyRuleset.objects.filter(name__startswith="Bench ").count()
    bench_match_ids = Match.objects.filter(series__name__startswith="Bench ").values_list(
        "id", flat=True
    )
    audits = FantasyCalcAudit.objects.filter(match_id__in=bench_match_ids).count()
    return rulesets + audits


def _clean_stale_seeds() -> None:
    """Delete leftover *deletable* benchmark rows from an interrupted run.

    The append-only tables, the audits/ledger and the frozen ruleset versions
    cannot be deleted (their DB triggers forbid it for every role, including
    the table owner) — a re-run against a dirty scratch DB must pass
    ``--reset`` instead. The deletable rows (derived read model, matches,
    teams, players, series) are always scrubbed.
    """
    from django.contrib.auth.models import User

    from apps.competitions.models import Series
    from apps.fantasy.models import PlayerMatchPerformance
    from apps.matches.models import Match, MatchAward, MatchPlayer, MatchResult, MatchTeam
    from apps.players.models import Player
    from apps.statistics.models import PlayerMatchStat
    from apps.teams.models import Team

    matches = Match.objects.filter(series__name__startswith="Bench ").distinct()
    match_ids = list(matches.values_list("id", flat=True))
    player_ids = list(
        Player.objects.filter(full_name__startswith="Bench ").values_list("id", flat=True)
    )
    team_ids = list(Team.objects.filter(name__startswith="Bench ").values_list("id", flat=True))
    series_ids = list(Series.objects.filter(name__startswith="Bench ").values_list("id", flat=True))

    if match_ids:
        PlayerMatchPerformance.objects.filter(match_id__in=match_ids).delete()
        MatchPlayer.objects.filter(match_id__in=match_ids).delete()
        MatchResult.objects.filter(match_id__in=match_ids).delete()
        MatchAward.objects.filter(match_id__in=match_ids).delete()
        MatchTeam.objects.filter(match_id__in=match_ids).delete()
        PlayerMatchStat.objects.filter(match_id__in=match_ids).delete()
        matches.delete()
    if player_ids:
        Player.objects.filter(id__in=player_ids).delete()
    if team_ids:
        Team.objects.filter(id__in=team_ids).delete()
    if series_ids:
        Series.objects.filter(id__in=series_ids).delete()
    User.objects.filter(username="bench-admin").delete()


def _bat_line(runs: int, balls: int, out: bool) -> dict[str, Any]:
    return {
        "rule_version": "bench",
        "player_id": "p",
        "did_bat": True,
        "runs": runs,
        "balls": balls,
        "fours": runs // 4,
        "sixes": runs // 20,
        "ones": 0,
        "twos": 0,
        "threes": 0,
        "fives": 0,
        "dots": 0,
        "out": out,
        "dismissal_type": "BOWLED" if out else None,
        "bowler_id": None,
        "fielder1_id": None,
        "fielder2_id": None,
        "fielder3_id": None,
        "retirement_type": None,
        "strike_rate": None,
    }


def _bowl_line(balls: int, runs_conceded: int, wickets: int) -> dict[str, Any]:
    return {
        "rule_version": "bench",
        "player_id": "p",
        "legal_balls": balls,
        "runs_conceded": runs_conceded,
        "wickets": wickets,
        "maidens": 0,
        "wides": 0,
        "noballs": 0,
        "dots": 0,
        "overs_display": f"{balls // 6}.{balls % 6}",
        "economy": None,
    }


def _seed(match_count: int) -> dict[str, Any]:
    """Seed ``match_count`` completed matches + a frozen default ruleset."""
    from tests.phase_c.factories import make_match, make_match_teams, make_player, make_team

    from apps.competitions.models import Series
    from apps.fantasy import authoring
    from apps.matches.models import MatchAward, MatchPlayer, MatchResult
    from apps.statistics.models import PlayerMatchStat

    _clean_stale_seeds()
    leftover = _leftover_fantasy_rows()
    if leftover and not args.reset:
        raise SystemExit(
            f"{leftover} immutable fantasy rows from an earlier run are present; "
            "re-run with --reset (drops + re-applies the fantasy migrations)"
        )
    team_pool = max(TEAM_POOL, match_count // 25)
    player_pool = max(PLAYER_POOL, match_count // 8)
    teams = [make_team(f"Bench Team {i}") for i in range(team_pool)]
    players = [make_player(f"Bench Player {i}") for i in range(player_pool)]

    actor = _admin()
    ruleset = authoring.create_ruleset(
        name="Bench Ruleset", description="benchmark rules", actor=actor
    )
    version = authoring.create_version(
        ruleset=ruleset,
        rules=RULES,
        policies={"super_over": "include", "abandoned": "compute"},
        format_scope="T20I",
        actor=actor,
    )
    authoring.set_default_version(version, actor=actor)
    authoring.freeze_version(version, actor=actor)

    matches: list[Any] = []
    series_count = max(1, -(-match_count // MATCHES_PER_SERIES))
    for s in range(series_count):
        series = Series.objects.create(name=f"Bench Series {s}", is_tournament=True)
        for i in range(min(MATCHES_PER_SERIES, match_count - len(matches))):
            t1 = teams[i % team_pool]
            t2 = teams[(i + 1) % team_pool]
            match = make_match(
                format_type="T20I",
                status="COMPLETED",
                series=series,
            )
            make_match_teams(match, t1, t2)
            MatchResult.objects.create(
                match=match,
                result_type="WIN",
                winner_team=t1 if i % 2 == 0 else t2,
            )
            MatchAward.objects.create(
                match=match,
                player=players[i % player_pool],
                award_type="player_of_match",
            )
            p1, p2 = players[i % player_pool], players[(i + 3) % player_pool]
            p3, p4 = players[(i + 5) % player_pool], players[(i + 7) % player_pool]
            for player, team, runs, balls, out, wickets in (
                (p1, t1, (i % 90) + 10, 40, True, i % 3),
                (p2, t1, (i % 70) + 5, 35, False, 0),
                (p3, t2, (i % 60) + 15, 30, True, (i + 1) % 3),
                (p4, t2, (i % 50) + 1, 25, False, 0),
            ):
                MatchPlayer.objects.create(
                    match=match, player=player, team=team, is_playing_xi=True
                )
                PlayerMatchStat.objects.create(
                    match=match,
                    player=player,
                    team=team,
                    did_bat=True,
                    did_bowl=wickets > 0,
                    did_field=True,
                    is_playing_xi=True,
                    batting=[_bat_line(runs, balls, out)],
                    bowling=[_bowl_line(24, (i % 40) + 10, wickets)] if wickets else [],
                    fielding={},
                    is_partial=False,
                )
            matches.append(match)
    return {
        "series": matches[0].series,
        "players": players,
        "teams": teams,
        "matches": matches,
        "version": version,
    }


def _cleanup(seed: dict[str, Any]) -> None:
    _clean_stale_seeds()


# --------------------------------------------------------------------------- #
# Measurements
# --------------------------------------------------------------------------- #


def bench_backfill(seed: dict[str, Any]) -> None:
    from apps.fantasy.models import FantasyCalcAudit
    from apps.fantasy.services import recompute

    started = _time.perf_counter()
    count = recompute.backfill_matches("all")
    elapsed = _time.perf_counter() - started
    rate = count / elapsed if elapsed else 0.0
    print(f"\nTier-2 backfill: {count} matches in {elapsed:.2f}s ({rate:.0f} matches/s)")
    for target in EXTRAPOLATE_TO:
        print(f"  linear extrapolation to {target:>7,} matches: ~{target / rate / 60:.1f} min")

    # ``compute_ms`` is frozen on the audit in milliseconds; _report expects
    # seconds (it renders *1e3).
    compute_ms = [
        float(run["timings"]["compute_ms"])
        for run in FantasyCalcAudit.objects.values("timings")
        if run["timings"] and run["timings"].get("compute_ms") is not None
    ]
    print("\nCompute time per match (frozen on the append-only calc audit):")
    _report("engine compute_ms", [ms / 1e3 for ms in compute_ms])


def _get_within_bucket(client: Any, url: str) -> Any:
    """GET ``url``, waiting out the token bucket (10/s, burst 120) on a 429.

    The sample timer starts only once the request is actually admitted, so a
    throttle wait never skews the measured latency.
    """
    for _ in range(6):
        response = client.get(url)
        if response.status_code != 429:
            return response
        retry_after = float(response.headers.get("Retry-After", "0.2") or 0.2)
        _time.sleep(retry_after + 0.05)
    raise AssertionError(f"rate limited for too long: {url}")


def bench_latency(seed: dict[str, Any]) -> None:
    from django.core.cache import caches
    from django.test import Client

    client = Client()
    match_id = seed["matches"][0].id
    endpoints = {
        "rulesets /api/v1/fantasy/rulesets": "/api/v1/fantasy/rulesets",
        f"points /api/v1/fantasy/points?match={match_id}": (
            f"/api/v1/fantasy/points?match={match_id}"
        ),
        f"match /api/v1/fantasy/matches/{match_id}": f"/api/v1/fantasy/matches/{match_id}",
    }
    print()
    for label, url in endpoints.items():
        # -- uncached: cold build per sample --------------------------------
        cold: list[float] = []
        for _ in range(args.samples):
            caches["default"].clear()
            caches["cache"].clear()
            started = _time.perf_counter()
            response = _get_within_bucket(client, url)
            cold.append(_time.perf_counter() - started)
            assert response.status_code == 200, f"{label} (cold) -> {response.status_code}"
            # Keep sustained demand at the bucket's refill rate (10/s).
            _time.sleep(0.1)
        _report(f"{label}  [uncached]", cold)

        # -- cached: warm once, then sample the cache-hit path --------------
        caches["default"].clear()
        caches["cache"].clear()
        _get_within_bucket(client, url)  # warm
        warm: list[float] = []
        for _ in range(args.samples):
            started = _time.perf_counter()
            response = _get_within_bucket(client, url)
            warm.append(_time.perf_counter() - started)
            assert response.status_code == 200, f"{label} (warm) -> {response.status_code}"
            _time.sleep(0.1)
        _report(f"{label}  [cached]", warm)


def explain_gates(seed: dict[str, Any]) -> None:
    """EXPLAIN the read path; gate on no seq scan of player_match_performance."""
    from django.db import connection

    match_id = seed["matches"][0].id
    version_id = seed["version"].id
    queries = {
        "match-endpoint PMP lookup (match_id + version, ordered)": (
            "SELECT * FROM player_match_performance "
            f"WHERE match_id = '{match_id}' AND ruleset_version_id = '{version_id}' "
            "ORDER BY total_points DESC"
        ),
        "points-endpoint PMP lookup (match_id, ordered)": (
            "SELECT * FROM player_match_performance "
            f"WHERE match_id = '{match_id}' "
            "ORDER BY computed_at DESC, match_id, player_id"
        ),
        "latest calc-run lookup (match_id, ordered, limit 1)": (
            "SELECT * FROM fantasy_calc_audit "
            f"WHERE match_id = '{match_id}' "
            "ORDER BY computed_at DESC, id LIMIT 1"
        ),
    }
    print("\nEXPLAIN gates:")
    gate_failed = False
    with connection.cursor() as cursor:
        for label, sql in queries.items():
            cursor.execute(f"EXPLAIN {sql}")
            plan = " | ".join(row[0] for row in cursor.fetchall())
            seq_scan = "Seq Scan on player_match_performance" in plan
            print(f"  {label}\n    {plan}")
            if "player_match_performance" in sql and seq_scan:
                gate_failed = True
                print("    !! GATE FAILED: sequential scan on player_match_performance")
            elif "fantasy_calc_audit" in sql and "Seq Scan" in plan:
                # The §3.8 DDL deliberately adds no index here; report only.
                print("    (observation: seq scan on the append-only calc audit — no index in DDL)")
    if gate_failed:
        raise SystemExit("EXPLAIN gate failed: player_match_performance read path is a seq scan")


def main() -> None:
    if args.reset:
        _reset_fantasy_tables()
    seed = _seed(args.matches)
    try:
        bench_backfill(seed)
        bench_latency(seed)
        explain_gates(seed)
    finally:
        _cleanup(seed)
        print("\ncleaned up deletable benchmark rows (append-only rows stay; re-run with --reset)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches", type=int, default=DEFAULT_MATCHES)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="drop + re-apply the fantasy migrations first (scrub append-only/frozen rows)",
    )
    args = parser.parse_args()

    import django

    django.setup()
    main()

#!/usr/bin/env python3
"""Benchmark the Phase D live write path + live read feeds (report-only).

Measures, against a scratch database (never the real one):

1. **Ingest-to-event-log latency** — wall time of ``apply_batch`` (delivery
   append + Tier-1 rebuild + event-log rows all commit together) and of the
   event-log append alone; reports p50/p95/mean across rounds.
2. **Tier-1 rebuild per delivery** — ``rebuild_innings`` cost on a full T20
   (120 balls) and ODI (300 balls) innings, per delivery.
3. **Throughput vs 10× peak provider rate** — sustained ``apply_batch``
   ingestion rate in deliveries/sec compared with the Growth-stage peak
   (CRICKET_PERFORMANCE_AND_SCALING §1: ~500k deliveries/day ≈ 5.8/s; 10× ≈
   58/s); asserts zero DLQ growth and monotonic event-log ``seq``.
4. **/matches/live with 50 matches** — response latency, constant query count
   (no N+1) and ``EXPLAIN`` of the hot query (indexed scan on
   ``(status, start_time_utc)``).

Usage:
    DATABASE_URL=postgres://.../cricket_bench \
        uv run python scripts/benchmark_live_path.py

The target database must already be migrated (``manage.py migrate``). The
script creates its own matches/teams/players in the scratch DB and does not
touch real data.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

#: Growth-stage peak provider rate (deliveries/day, PERF §1) -> per second.
PEAK_DELIVERIES_PER_SECOND = 500_000 / 86_400
#: The ingestion-throughput success criterion (PERF §12): 10x peak.
TARGET_CAPACITY_PER_SECOND = 10 * PEAK_DELIVERIES_PER_SECOND

LIVE_MATCH_COUNT = 50
ROUNDS = 60


def _pctile(samples: list[float], pct: float) -> float:
    if len(samples) == 1:
        return samples[0]
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def _report(name: str, samples: list[float]) -> None:
    mean = statistics.fmean(samples)
    print(
        f"{name:<38} n={len(samples):>4}  mean={mean * 1e3:8.2f}ms  "
        f"p50={_pctile(samples, 50) * 1e3:8.2f}ms  p95={_pctile(samples, 95) * 1e3:8.2f}ms"
    )


def _make_context() -> dict[str, object]:
    from tests.phase_c.factories import (
        make_match,
        make_match_teams,
        make_player,
        make_team,
    )

    from apps.core.enums import MatchStatus
    from apps.deliveries.services.validation import DeliveryDraft

    match = make_match(format_type="T20I", overs_limit=20, status=MatchStatus.IN_PROGRESS)
    team1 = make_team("Bench Alpha")
    team2 = make_team("Bench Beta")
    make_match_teams(match, team1, team2)
    batter = make_player("Bench Batter")
    non_striker = make_player("Bench Non Striker")
    bowler = make_player("Bench Bowler")
    return {
        "match": match,
        "team1": team1,
        "team2": team2,
        "batter": batter,
        "non_striker": non_striker,
        "bowler": bowler,
        "DeliveryDraft": DeliveryDraft,
    }


def _batch(ctx: dict[str, object], seqs: list[int], innings: int = 1) -> object:
    from apps.live.services.apply import LiveInningsBatch

    Draft = ctx["DeliveryDraft"]
    drafts = tuple(
        Draft(
            delivery_seq=seq,
            over_number=(seq - 1) // 6 + 1,
            ball_in_over=(seq - 1) % 6 + 1,
            bowler_id=ctx["bowler"].id,
            batter_id=ctx["batter"].id,
            non_striker_id=ctx["non_striker"].id,
            runs_off_bat=seq % 5,
        )
        for seq in seqs
    )
    return LiveInningsBatch(innings_number=innings, drafts=drafts)


def bench_ingest_to_event_log() -> list[float]:
    """Round-trip latency of apply_batch (deliveries + rebuild + event rows)."""
    from apps.live.services.apply import apply_batch
    from apps.matches.services import event_log

    ctx = _make_context()
    match = ctx["match"]

    samples: list[float] = []
    for round_no in range(ROUNDS):
        seq = round_no % 12 + 1
        started = time.perf_counter()
        apply_batch(match, [_batch(ctx, [seq])])
        samples.append(time.perf_counter() - started)

    # The event-log append alone (already committed inside apply_batch, so
    # this measures the append primitive directly on a fresh log position).
    event_samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        event_log.append_event(match.id, "match.updated", {"fields": {"note": "bench"}})
        event_samples.append(time.perf_counter() - started)

    print("\n=== ingest-to-event-log latency (per delivery) ===")
    _report("apply_batch (append+rebuild+log)", samples)
    _report("event_log.append_event (log only)", event_samples)
    print(f"(Growth-stage 10x peak = {TARGET_CAPACITY_PER_SECOND:.1f} deliveries/s)")
    return samples


def bench_rebuild_per_delivery() -> None:
    """Tier-1 rebuild cost per delivery on full T20 / ODI innings."""
    from apps.live.services.apply import apply_batch

    print("\n=== Tier-1 rebuild per delivery (full per-innings rebuild) ===")

    for label, balls in (("T20 innings (120 balls)", 120), ("ODI innings (300 balls)", 300)):
        ctx = _make_context()
        match = ctx["match"]
        # Ingest the innings in 6-ball over-chunks, timing only the last chunk
        # (a full-innings rebuild of all stored balls).
        seqs = list(range(1, balls + 1))
        for start in range(0, balls, 6):
            apply_batch(match, [_batch(ctx, seqs[start : start + 6])])

        from apps.scorecards.services.rebuild import rebuild_innings

        innings = match.innings.get(innings_number=1)
        started = time.perf_counter()
        rebuild = rebuild_innings(innings)
        elapsed = time.perf_counter() - started
        assert rebuild.digest
        per_ball = elapsed / balls * 1e6
        print(f"{label:<38} total={elapsed * 1e3:8.2f}ms  per-delivery={per_ball:7.1f}µs")


def bench_throughput() -> None:
    """Sustained ingestion capacity vs the 10x-peak target; zero DLQ growth."""
    from apps.ingestion.models import IngestionError
    from apps.live.services.apply import apply_batch
    from apps.matches.models import MatchEventLog

    dlq_before = IngestionError.objects.count()
    log_before = MatchEventLog.objects.count()

    # A live poll delivers a few balls per cycle (an over at a time here). Each
    # apply_batch carries 6 fresh deliveries on a rotating match so seqs never
    # repeat and no innings exceeds its 120-ball limit.
    contexts: list[dict[str, object]] = []
    next_seq: dict[str, int] = {}
    deliveries = 0
    applied_ok = True
    started = time.perf_counter()
    for _ in range(200):
        if not contexts or next_seq[contexts[-1]["match"].id] >= 120:
            contexts.append(_make_context())
        ctx = contexts[-1]
        match = ctx["match"]
        start_seq = next_seq.get(match.id, 1)
        seqs = list(range(start_seq, start_seq + 6))
        next_seq[match.id] = start_seq + 6
        report = apply_batch(match, [_batch(ctx, seqs)])
        applied_ok = applied_ok and report.appended == 6
        deliveries += 6
    elapsed = time.perf_counter() - started
    rate = deliveries / elapsed

    dlq_after = IngestionError.objects.count()
    log_after = MatchEventLog.objects.count()
    # ``seq`` is per-match: each match's log must be 1..N contiguous.
    rows = list(MatchEventLog.objects.order_by("match_id", "seq").values_list("match_id", "seq"))
    monotonic = True
    per_match: dict[str, list[int]] = {}
    for match_id, seq in rows:
        per_match.setdefault(str(match_id), []).append(seq)
    for seqs in per_match.values():
        if seqs != list(range(1, len(seqs) + 1)):
            monotonic = False
            break

    print("\n=== ingestion throughput (sustained apply_batch, 6-ball overs) ===")
    print(f"deliveries ingested : {deliveries}")
    print(f"apply calls         : 200 (all appended 6, no duplicates: {applied_ok})")
    print(f"wall time           : {elapsed:.2f}s")
    print(f"capacity            : {rate:.1f} deliveries/s")
    print(
        f"target (10x Growth peak {PEAK_DELIVERIES_PER_SECOND:.2f}/s): "
        f"{TARGET_CAPACITY_PER_SECOND:.1f} deliveries/s"
    )
    print(f"DLQ growth          : {dlq_after - dlq_before} (must be 0)")
    print(f"event-log rows      : {log_after - log_before} (durable append)")
    if rate < TARGET_CAPACITY_PER_SECOND:
        raise SystemExit(
            f"throughput {rate:.1f}/s below 10x-peak target {TARGET_CAPACITY_PER_SECOND:.1f}/s"
        )
    if not applied_ok:
        raise SystemExit("some apply calls did not append exactly 6 deliveries")
    if dlq_after != dlq_before:
        raise SystemExit(f"DLQ grew by {dlq_after - dlq_before} rows during the benchmark")
    if not monotonic:
        raise SystemExit("event-log seq is not monotonic")


def bench_live_feed() -> None:
    """/matches/live with 50 live matches: latency, no N+1, EXPLAIN."""
    from django.db import connection
    from django.test import Client
    from django.test.utils import CaptureQueriesContext
    from tests.phase_c.factories import make_match, make_match_teams, make_team

    from apps.core.enums import MatchStatus

    for i in range(LIVE_MATCH_COUNT):
        match = make_match(
            format_type="T20I",
            overs_limit=20,
            status=MatchStatus.IN_PROGRESS,
        )
        make_match_teams(match, make_team(f"Live A {i}"), make_team(f"Live B {i}"))

    client = Client()
    latencies: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        response = client.get("/api/v1/matches/live", data={"limit": 50})
        latencies.append(time.perf_counter() - started)
    assert response.status_code == 200

    with CaptureQueriesContext(connection) as captured:
        client.get("/api/v1/matches/live", data={"limit": 50})
    queries = len(captured.captured_queries)

    print("\n=== /matches/live with 50 live matches ===")
    _report("GET /matches/live?limit=50", latencies)
    print(f"query count         : {queries} for one page (constant — no N+1; suite gate is <= 12)")

    # EXPLAIN (ANALYZE, BUFFERS) the hot query: indexed scan on
    # (status, start_time_utc) -> live matches. ``sql_with_params`` + mogrify
    # yields executable SQL (str(queryset.query) is debug-only, not runnable).
    from apps.matches.selectors.reads import list_live_matches

    queryset = list_live_matches()
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        literal_sql = str(cursor.mogrify(sql, params))
        cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + literal_sql)
        plan = "\n".join(row[0] for row in cursor.fetchall())
    print("--- EXPLAIN (ANALYZE, BUFFERS) ---")
    print(plan)

    # The spec's indexed-scan precondition: ix_match_status_start exists.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE indexname = %s", ["ix_match_status_start"]
        )
        index_exists = cursor.fetchone() is not None
    print(f"index ix_match_status_start (status, start_time_utc DESC): {index_exists}")
    if queries > 12:
        raise SystemExit(f"query count {queries} exceeds the no-N+1 gate")
    if not index_exists:
        raise SystemExit("ix_match_status_start missing — the indexed-scan precondition fails")


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
    import django

    django.setup()

    bench_ingest_to_event_log()
    bench_rebuild_per_delivery()
    bench_throughput()
    bench_live_feed()
    print("\nAll live-path benchmark gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

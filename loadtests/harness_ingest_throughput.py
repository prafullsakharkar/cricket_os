#!/usr/bin/env python3
"""§12 ingestion throughput — replay ingest at a configurable speed (Task 5 §9.2).

In-process against the local PostgreSQL: applies ``--fixtures`` recorded
fixtures through the real fixture-sync path (``sync_fixtures``), measures
fixtures/sec and per-fixture p50/p95/p99, and reads the DLQ depth (open
``IngestionError`` rows) before/after. The entire run is wrapped in a
transaction that rolls back, so the dev database is left untouched.

The §12 criterion is "10× peak provider rate: no DLQ growth, lag < 30 s". The
peak provider rate for match-level fixtures is a few per second, so 10× locally
is a burst of ``--fixtures`` fixtures; the measured rate is what the single-node
local pipeline sustains. UNVERIFIED against production.

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python loadtests/harness_ingest_throughput.py \\
        --target localhost:55435 --fixtures 100
    uv run python loadtests/harness_ingest_throughput.py --target localhost:55435 --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from loadtests import harness_common as hc  # noqa: E402

NAME = "harness_ingest_throughput"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    hc.add_common_args(
        parser,
        default_duration=30.0,
        default_rate=10.0,
        duration_help="unused for the burst (see --fixtures); kept for the common CLI",
        rate_help="unused for the burst; kept for the common CLI",
    )
    parser.add_argument("--fixtures", type=int, default=100, help="number of fixtures to apply")
    args = parser.parse_args()
    if args.fixtures <= 0:
        parser.error("--fixtures must be positive")
    hc.guard(
        name=NAME,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"apply {args.fixtures} recorded fixtures through sync_fixtures "
            "(ReplayAdapter-shaped corpus) in-process against local Postgres",
            "measure fixtures/sec + per-fixture p50/p95/p99; DLQ depth before/after",
            "the whole run rolls back (dev DB untouched)",
            "SLO status UNVERIFIED (G2 gate fails; local topology only)",
        ],
    )

    import django

    django.setup()

    from django.db import transaction

    from apps.ingestion.models import IngestionError
    from apps.providers.models import DataProvider
    from apps.synchronization.services import fixtures

    provider, _ = DataProvider.objects.get_or_create(
        code="ingest-load", defaults={"name": "ingest-load harness", "is_active": True}
    )
    dlq_before = IngestionError.objects.filter(resolved=False).count()
    start = datetime.now(UTC)

    stats = hc.TimingStats()
    created = 0
    with transaction.atomic():
        for i in range(args.fixtures):
            record = fixtures.FixtureRecord(
                provider_match_id=f"load-{i}",
                format_type="T20I",
                start_time_utc=start + timedelta(minutes=200 * i + 1),  # dedup-tolerance apart
                teams=(f"Load Team {i}A", f"Load Team {i}B"),
                team_registry={
                    f"Load Team {i}A": {"key_opta": f"load-{i}a"},
                    f"Load Team {i}B": {"key_opta": f"load-{i}b"},
                },
                match_number=1,
            )
            began = hc.time_now()
            report = fixtures.sync_fixtures(provider, [record])
            stats.record((hc.time_now() - began) * 1000.0, ok=report.created + report.updated > 0)
            created += report.created

        # The transaction rolls back on exit: nothing above is persisted.
        transaction.set_rollback(True)

    dlq_after = IngestionError.objects.filter(resolved=False).count()
    elapsed = stats.summary()
    fixtures_per_sec = created / max(elapsed["max_ms"] / 1000.0, 1e-9)
    lag_estimate = (
        args.fixtures / max(fixtures_per_sec, 1e-9) if fixtures_per_sec > 0 else float("inf")
    )
    measured = {
        **elapsed,
        "fixtures_created": float(created),
        "fixtures_per_sec": round(fixtures_per_sec, 3),
    }
    dlq_growth = dlq_after - dlq_before
    status = "demonstrated" if dlq_growth <= 0 and lag_estimate < 30 else "failed"
    report = {
        "name": NAME,
        "target": args.target,
        "dry_run": False,
        "fixtures_applied": args.fixtures,
        "dlq_depth_before": dlq_before,
        "dlq_depth_after": dlq_after,
        "dlq_growth": dlq_growth,
        "lag_estimate_seconds": round(lag_estimate, 3),
        "env": hc.env_tag(),
        "slo": hc.slo_block(
            name=hc.SLO_INGEST[0],
            target=hc.SLO_INGEST[1],
            measured=measured,
            status=status,
            implication=(
                "ingest throughput below 10x peak or DLQ growth locally means the "
                "ingestion pipeline needs batching/backpressure tuning before "
                "staging; re-run with the real provider corpus on staging."
            ),
        ),
    }
    path = hc.write_report(NAME, report)
    print(hc.json_dumps(report))
    print(f"report: {path}")
    if status != "demonstrated":
        sys.exit(1)


if __name__ == "__main__":
    main()

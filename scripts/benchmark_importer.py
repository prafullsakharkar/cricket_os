#!/usr/bin/env python3
"""Benchmark the Cricsheet register importer (report-only, not in make check).

Generates a synthetic 30k-row people register, then runs the pure
``import_register`` core through the Django sink against a scratch database
and reports wall time + peak RSS (ru_maxrss). The numbers feed the Phase B
final report (item c).

Usage:
    DATABASE_URL=postgres://.../cricket_bench \\
        uv run python scripts/benchmark_importer.py [--rows 30000]

The target database must already be migrated (``manage.py migrate``). Point
``DATABASE_URL`` at a scratch DB so the benchmark never touches real data; the
script does not wipe anything.
"""

from __future__ import annotations

import argparse
import csv
import os
import resource
import sys
import time
from pathlib import Path

#: Synthetic rows written to /tmp/opencode (approved scratch area).
OUT_DIR = Path(os.environ.get("TMPDIR", "/tmp/opencode"))
ROWS = 30_000


def generate_people(path: Path, n: int) -> None:
    """Write ``n`` realistic-looking people.csv rows."""
    first = ["Virat", "Rohit", "Jasprit", "Ben", "Jos", "Kane", "Trent", "Babar", "Shakib", "Pat"]
    last = [
        "Kohli",
        "Sharma",
        "Bumrah",
        "Stokes",
        "Buttler",
        "Williamson",
        "Boult",
        "Azam",
        "Hasan",
        "Cummins",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "identifier",
                "name",
                "unique_name",
                "key_bcci",
                "key_opta",
                "key_pulse",
                "key_cricbuzz",
            ]
        )
        for i in range(1, n + 1):
            writer.writerow(
                [
                    f"p-{i:06d}",
                    f"{first[i % 10]} {last[i % 10]}",
                    f"U{i}",
                    f"bcci-{i}",
                    f"opt-{i}",
                    f"kp-{i}",
                    f"cbz-{i}",
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=ROWS)
    parser.add_argument("--provider", default="cricsheet")
    args = parser.parse_args()

    # Django bootstrap: put the repo root on sys.path (running the script
    # directly puts ``scripts/`` there, not the root), then load settings
    # from the environment like manage.py does.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    import django

    django.setup()

    from django.db import transaction

    from apps.players.import_register import import_register
    from apps.players.sinks import DjangoRegisterSink

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    register = OUT_DIR / "benchmark_people.csv"
    print(f"generating {args.rows} rows -> {register}")
    generate_people(register, args.rows)

    with register.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    sink = DjangoRegisterSink(provider_code=args.provider)
    started = time.monotonic()
    with transaction.atomic():
        report = import_register(rows, sink, kind="people")
    elapsed = time.monotonic() - started

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print("\n=== benchmark_importer report ===")
    print(f"rows read      : {report.read}")
    print(f"created        : {report.created}")
    print(f"updated        : {report.updated}")
    print(f"errors         : {report.failed}")
    print(f"wall time      : {elapsed:.2f}s  ({report.read / elapsed:.0f} rows/s)")
    print(f"peak RSS       : {rss_kb / 1024:.1f} MiB")
    print("================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())

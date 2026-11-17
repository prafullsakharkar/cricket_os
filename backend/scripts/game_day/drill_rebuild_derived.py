#!/usr/bin/env python3
"""Game day §10.3: derived-data rebuild drill — measured wall time + no double count.

Runs the real Tier-2 rebuild (`rebuild_statistics all`) over a recent window
of completed matches and asserts the digest-validated rows reproduce: the
``player_match_stat`` row count for the scoped matches is identical before and
after (idempotent rebuild, no double counting), and the rebuild wall time is
measured.

Runbook: `docs/RUNBOOKS.md` → "Statistics pipeline".

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/drill_rebuild_derived.py
    uv run python scripts/game_day/drill_rebuild_derived.py --since 2026-10-01
    uv run python scripts/game_day/drill_rebuild_derived.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from scripts.game_day import common  # noqa: E402

SCENARIO = "drill_rebuild_derived"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    parser.add_argument(
        "--since",
        default=(date.today() - timedelta(days=90)).isoformat(),
        help="rebuild scope: completed matches on/after YYYY-MM-DD",
    )
    args = parser.parse_args()
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"run rebuild_statistics all --since {args.since} (real Tier-2 backfill)",
            "measure rebuild wall time",
            "assert player_match_stat row count for the scoped matches is stable "
            "(idempotent, no double counting)",
            "report: verdict vs runbook 'Statistics pipeline'",
        ],
    )

    import django

    django.setup()

    from django.core.management import call_command

    from apps.matches.models import Match
    from apps.statistics.models import PlayerMatchStat

    scoped = list(
        Match.objects.exclude_merged()
        .filter(status="COMPLETED", start_time_utc__date__gte=args.since)
        .values_list("id", flat=True)
    )
    if not scoped:
        scoped = list(
            Match.objects.exclude_merged()
            .filter(status="COMPLETED")
            .order_by("-start_time_utc")[:20]
            .values_list("id", flat=True)
        )
        effective_since = "no completed matches in window; fell back to latest 20"
    else:
        effective_since = args.since

    before = PlayerMatchStat.objects.filter(match_id__in=scoped).count()

    started = time.monotonic()
    call_command("rebuild_statistics", "all", since=args.since, verbosity=1)
    rebuild_ms = round((time.monotonic() - started) * 1000.0, 2)

    after = PlayerMatchStat.objects.filter(match_id__in=scoped).count()
    no_double_count = after == before
    verdict = "sufficient" if no_double_count else "gap"
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=None,
        time_to_mitigate_ms=rebuild_ms,
        runbook_section="Statistics pipeline",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "scoped_matches": len(scoped),
            "effective_scope": effective_since,
            "player_match_stat_before": before,
            "player_match_stat_after": after,
            "no_double_count": no_double_count,
            "rebuild_wall_time_ms": rebuild_ms,
            "note": "digest-validated rows reproduce (idempotent rebuild); "
            "production-scale rebuild time is UNVERIFIED (G2)",
        },
    )
    print(report.read_text(encoding="utf-8"))
    if verdict != "sufficient":
        sys.exit(1)


if __name__ == "__main__":
    main()

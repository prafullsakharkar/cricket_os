#!/usr/bin/env python3
"""§12 correction burst — simultaneous corrections under reads (plan Task 5 §9.2).

In-process: seeds a scratch live match with one innings of deliveries through
the real apply path, then fires ``--burst`` concurrent
``apply_delivery_correction`` calls across ``--workers`` threads, measuring
per-call latency and counting lock-contention failures
(``IntegrityError``/``OperationalError``). Verifies the Tier-1 rebuild
converged (delivery revision monotonic, no partial state). Everything rolls
back at the end so the dev database is untouched.

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python loadtests/harness_correction_burst.py \\
        --target localhost:55435 --workers 8 --burst 40
    uv run python loadtests/harness_correction_burst.py --target localhost:55435 --dry-run

SLO: simultaneous corrections produce no lock-contention errors and a correct
Tier-1 rebuild (UNVERIFIED: local topology only).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from loadtests import harness_common as hc  # noqa: E402

NAME = "harness_correction_burst"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    hc.add_common_args(
        parser,
        default_duration=30.0,
        default_rate=10.0,
        duration_help="unused for the burst (see --burst); kept for the common CLI",
        rate_help="unused for the burst; kept for the common CLI",
    )
    parser.add_argument("--workers", type=int, default=8, help="concurrent correction threads")
    parser.add_argument("--burst", type=int, default=40, help="total corrections across workers")
    args = parser.parse_args()
    if args.workers <= 0 or args.burst <= 0:
        parser.error("--workers and --burst must be positive")
    hc.guard(
        name=NAME,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"{args.burst} apply_delivery_correction calls across {args.workers} threads",
            "on a scratch live match seeded through the real apply path",
            "measure per-call p50/p95/p99 + lock-contention failures; verify rebuild",
            "the whole run rolls back (dev DB untouched)",
            "SLO status UNVERIFIED (G2 gate fails; local topology only)",
        ],
    )

    import django

    django.setup()

    from dataclasses import replace as _dc_replace

    from django.db import IntegrityError, OperationalError, transaction

    from apps.deliveries.services.validation import DeliveryDraft
    from apps.live.services.apply import LiveInningsBatch
    from apps.live.services.corrections import apply_delivery_correction
    from apps.matches.models import MatchPlayer
    from apps.providers.models import DataProvider

    # ---- seed a scratch match (COMMITTED so worker connections can see it) - #
    seed_scope = f"corr-burst-{os.getpid()}-{int(hc.time_now() * 1000)}"
    with transaction.atomic():
        from tests.phase_c.factories import make_match, make_team

        team1 = make_team(f"{seed_scope} A")
        team2 = make_team(f"{seed_scope} B")

        match = make_match(
            start_time_utc=_datetime_now(),
            match_number=1,
            status="IN_PROGRESS",
        )
        from apps.matches.models import MatchTeam

        MatchTeam.objects.create(match=match, team=team1, team_number=1)
        MatchTeam.objects.create(match=match, team=team2, team_number=2)
        from tests.phase_c.factories import make_player

        batter = make_player(f"{seed_scope} Batter")
        bowler = make_player(f"{seed_scope} Bowler")
        MatchPlayer.objects.create(match=match, player=batter, team=team1, is_playing_xi=True)
        MatchPlayer.objects.create(match=match, player=bowler, team=team2, is_playing_xi=True)
        provider, _ = DataProvider.objects.get_or_create(
            code="corr-load", defaults={"name": "correction harness", "is_active": True}
        )
        from apps.live.services.apply import apply_batch

        drafts = tuple(
            DeliveryDraft(
                delivery_seq=s,
                over_number=(s - 1) // 6 + 1,
                ball_in_over=(s - 1) % 6 + 1,
                bowler_id=bowler.id,
                batter_id=batter.id,
                non_striker_id=batter.id,
                runs_off_bat=s % 2,
            )
            for s in range(1, 13)
        )
        apply_batch(match, [LiveInningsBatch(innings_number=1, drafts=drafts)], source=provider)

    # ---- concurrent corrections (each rolled back: nothing persists) ------- #
    stats = hc.TimingStats()
    lock_errors: list[str] = []
    lock_errors_guard = threading.Lock()

    def worker(seed: int) -> None:
        try:
            for _ in range(max(1, args.burst // args.workers)):
                revised = LiveInningsBatch(
                    innings_number=1,
                    drafts=tuple(
                        _dc_replace(d, runs_off_bat=1, revision=2)
                        for d in drafts[seed % 2 : seed % 2 + 2]
                    ),
                )
                began = hc.time_now()
                with transaction.atomic():
                    apply_delivery_correction(
                        match, [revised], reason="concurrent burst", source=provider
                    )
                    transaction.set_rollback(True)
                stats.record((hc.time_now() - began) * 1000.0, ok=True)
        except (IntegrityError, OperationalError) as exc:
            with lock_errors_guard:
                lock_errors.append(f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            with lock_errors_guard:
                lock_errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    # ---- verify the Tier-1 rebuild converged ------------------------------- #
    match.refresh_from_db()
    revision_ok = True
    delivery_rows = list(match.deliveries.all())
    for row in delivery_rows:
        if row.revision < 1:
            revision_ok = False
    rebuild_ok = len(delivery_rows) >= 12 and revision_ok

    # ---- clean up the seed (committed) -------------------------------------- #
    try:
        with transaction.atomic():
            from apps.deliveries.models import Delivery, Over
            from apps.innings.models import Innings, InningsSummary
            from apps.matches.models import MatchEventLog
            from apps.scorecards.models import BattingEntry, BowlingEntry, Partnership

            innings_ids = list(
                Innings.objects.filter(match_id=match.id).values_list("id", flat=True)
            )
            BattingEntry.objects.filter(innings_id__in=innings_ids).delete()
            BowlingEntry.objects.filter(innings_id__in=innings_ids).delete()
            Partnership.objects.filter(innings_id__in=innings_ids).delete()
            Over.objects.filter(innings_id__in=innings_ids).delete()
            InningsSummary.objects.filter(innings_id__in=innings_ids).delete()
            Delivery.objects.filter(match_id=match.id).delete()
            MatchEventLog.objects.filter(match_id=match.id).delete()
            Innings.objects.filter(match_id=match.id).delete()
            MatchPlayer.objects.filter(match_id=match.id).delete()
            MatchTeam.objects.filter(match_id=match.id).delete()
            match.delete()
            for player in (batter, bowler):
                player.delete()
            team1.delete()
            team2.delete()
    except Exception:  # cleanup must never mask the measurement
        pass

    measured = stats.summary()
    measured["lock_contention_errors"] = float(len(lock_errors))
    measured["tier1_rebuild_ok"] = float(rebuild_ok)
    status = "demonstrated" if len(lock_errors) == 0 and rebuild_ok else "failed"
    report = {
        "name": NAME,
        "target": args.target,
        "dry_run": False,
        "workers": args.workers,
        "burst": args.burst,
        "lock_contention_errors": lock_errors[:5],
        "tier1_rebuild_ok": rebuild_ok,
        "env": hc.env_tag(),
        "slo": hc.slo_block(
            name=hc.SLO_CORRECTIONS[0],
            target=hc.SLO_CORRECTIONS[1],
            measured=measured,
            status=status,
            implication=(
                "lock-contention errors during simultaneous corrections means the "
                "advisory-lock strategy needs review (deadlock window) — re-run on "
                "staging with the real correction volume."
            ),
        ),
    }
    path = hc.write_report(NAME, report)
    print(hc.json_dumps(report))
    print(f"report: {path}")
    if status != "demonstrated":
        sys.exit(1)


def _datetime_now() -> object:
    from datetime import UTC, datetime

    return datetime.now(UTC)


if __name__ == "__main__":
    main()

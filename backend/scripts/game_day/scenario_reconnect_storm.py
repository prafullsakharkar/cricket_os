#!/usr/bin/env python3
"""Game day §10.1: reconnect storm after a deploy restart.

Simulates a burst of clients reconnecting to a match after a deploy restart,
each advertising ``from_seq``. Verifies:

* **seq-based replay** — every reconnecting client is served exactly the
  events after its ``from_seq`` (``events_since``), never a full re-read;
* **no resync storm** — ``resync_required`` stays False for clients within
  the durable log (a resync signal is reserved for real gaps, not restarts);
* **replay latency** — measured per client.

Uses the production reader (`PostgresEventLogReader`) against a scratch
match's real durable event log. Runbook: `docs/RUNBOOKS.md` →
"Realtime resync".

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_reconnect_storm.py
    uv run python scripts/game_day/scenario_reconnect_storm.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from scripts.game_day import common  # noqa: E402

SCENARIO = "scenario_reconnect_storm"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    parser.add_argument("--clients", type=int, default=20, help="reconnecting clients")
    parser.add_argument("--behind", type=int, default=3, help="events each client is behind")
    args = parser.parse_args()
    if args.clients <= 0 or args.behind <= 0:
        parser.error("--clients and --behind must be positive")
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            "seed a scratch live match and append events to the durable log",
            f"simulate {args.clients} clients reconnecting from_seq = latest - {args.behind}",
            "verify each gets exactly the missing events (seq-based replay, no resync storm)",
            "report: replay latency p95 vs runbook 'Realtime resync'",
        ],
    )

    import django

    django.setup()

    import time

    from django.db import transaction
    from tests.phase_c.factories import make_innings, make_match, make_player, make_team

    from apps.deliveries.services.validation import DeliveryDraft
    from apps.live.services.apply import LiveInningsBatch, apply_batch
    from apps.matches.models import MatchEventLog, MatchPlayer, MatchTeam
    from apps.matches.services.stream_reader import PostgresEventLogReader

    # ---- seed a scratch match with a durable event log --------------------- #
    seed = f"GD Storm {os.getpid()}"
    with transaction.atomic():
        team1 = make_team(f"{seed} A")
        team2 = make_team(f"{seed} B")
        match = make_match(start_time_utc=common_dt(), match_number=1, status="IN_PROGRESS")
        MatchTeam.objects.create(match=match, team=team1, team_number=1)
        MatchTeam.objects.create(match=match, team=team2, team_number=2)
        batter = make_player(f"{seed} Batter")
        bowler = make_player(f"{seed} Bowler")
        MatchPlayer.objects.create(match=match, player=batter, team=team1, is_playing_xi=True)
        MatchPlayer.objects.create(match=match, player=bowler, team=team2, is_playing_xi=True)
        make_innings(match, team1, team2, innings_number=1)
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
            for s in range(1, 7)
        )
        apply_batch(match, [LiveInningsBatch(innings_number=1, drafts=drafts)], source=None)

    reader = PostgresEventLogReader()
    latest = reader.latest_seq(str(match.id))
    from_seq = max(latest - args.behind, 0)

    # ---- the storm: every client replays from its from_seq ------------------ #
    latencies: list[float] = []
    replay_counts: list[int] = []
    resyncs = 0
    for _ in range(args.clients):
        started = time.monotonic()
        info = reader.events_since(str(match.id), from_seq)
        latencies.append((time.monotonic() - started) * 1000.0)
        replay_counts.append(len(info.events))
        if info.resync_required:
            resyncs += 1

    p95 = sorted(latencies)[round(0.95 * (len(latencies) - 1))] if latencies else 0.0
    missing = latest - from_seq
    exact_replay = all(n == missing for n in replay_counts)
    no_resync_storm = resyncs == 0

    # ---- clean up ------------------------------------------------------------ #
    with transaction.atomic():
        from apps.deliveries.models import Delivery, Over
        from apps.innings.models import Innings, InningsSummary
        from apps.scorecards.models import BattingEntry, BowlingEntry, Partnership

        iids = list(Innings.objects.filter(match_id=match.id).values_list("id", flat=True))
        BattingEntry.objects.filter(innings_id__in=iids).delete()
        BowlingEntry.objects.filter(innings_id__in=iids).delete()
        Partnership.objects.filter(innings_id__in=iids).delete()
        Over.objects.filter(innings_id__in=iids).delete()
        InningsSummary.objects.filter(innings_id__in=iids).delete()
        Delivery.objects.filter(match_id=match.id).delete()
        MatchEventLog.objects.filter(match_id=match.id).delete()
        Innings.objects.filter(match_id=match.id).delete()
        MatchPlayer.objects.filter(match_id=match.id).delete()
        MatchTeam.objects.filter(match_id=match.id).delete()
        match.delete()
        batter.delete()
        bowler.delete()
        team1.delete()
        team2.delete()

    verdict = "sufficient" if exact_replay and no_resync_storm else "gap"
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=None,
        time_to_mitigate_ms=round(p95, 2),
        runbook_section="Realtime resync",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "clients": args.clients,
            "events_behind_each": args.behind,
            "latest_seq": latest,
            "from_seq": from_seq,
            "replay_p95_ms": round(p95, 2),
            "replay_frames_per_client_exact": exact_replay,
            "resync_required_count": resyncs,
            "no_resync_storm": no_resync_storm,
            "flow": "deploy restart -> clients reconnect with from_seq -> "
            "seq-based replay, no full re-read",
            "unverified_reason": "gate G2 FAILS: local topology only, not production evidence",
        },
    )
    print(report.read_text(encoding="utf-8"))
    if verdict != "sufficient":
        sys.exit(1)


def common_dt() -> object:
    from datetime import UTC, datetime

    return datetime.now(UTC)


if __name__ == "__main__":
    main()

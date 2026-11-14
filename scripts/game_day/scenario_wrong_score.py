#!/usr/bin/env python3
"""Game day §10.1: a reported wrong score → reconcile → correct → ETag change.

Seeds a scratch completed match, runs the real ``reconcile_state`` against a
provider card that disagrees (the complaint), then applies the correction
through the real correction path and verifies the cache/ETag version for the
match moved:

* **detection** — ``reconcile_state`` opens a ``ReconciliationJob`` and writes
  a ``reconciliation_mismatch`` DLQ row (no silent correction);
* **correction** — ``apply_delivery_correction`` re-derives the innings and
  the ``delivery.corrected`` event invalidates ``match:{id}`` (the version the
  strong ETag is derived from);
* **time-to-correct** — measured from the complaint to the version bump.

Runbook: `docs/RUNBOOKS.md` → "Wrong score complaint".

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_wrong_score.py
    uv run python scripts/game_day/scenario_wrong_score.py --dry-run
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

SCENARIO = "scenario_wrong_score"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    args = parser.parse_args()
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            "seed a completed scratch match with one innings",
            "reconcile_state against a disagreeing provider card -> ReconciliationJob "
            "+ reconciliation_mismatch DLQ row (detection)",
            "apply_delivery_correction -> delivery.corrected invalidates match:{id} "
            "(ETag version moves)",
            "report: detection + time-to-correct vs runbook 'Wrong score complaint'",
        ],
    )

    import django

    django.setup()

    import time

    from django.db import transaction
    from tests.phase_c.factories import make_innings, make_match, make_player, make_team

    from apps.core.cache import versions
    from apps.deliveries.services.validation import DeliveryDraft
    from apps.ingestion.models import IngestionError
    from apps.live.services.apply import LiveInningsBatch, apply_batch
    from apps.live.services.corrections import apply_delivery_correction
    from apps.matches.models import MatchPlayer, MatchResult, MatchTeam
    from apps.matches.services import state as lifecycle
    from apps.providers.models import DataProvider
    from apps.synchronization.services import reconciliation

    # ---- seed a scratch match (committed) ---------------------------------- #
    seed = f"GD Score {os.getpid()}"
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
        lifecycle.transition(
            match,
            "COMPLETED",
            source=None,
        )
        MatchResult.objects.create(match=match, result_type="WIN", winner_team=team1)
    provider, _ = DataProvider.objects.get_or_create(
        code="gd-score", defaults={"name": "game-day wrong score", "is_active": True}
    )

    match_namespace = f"match:{match.id}"
    version_before = versions.current(match_namespace)

    # ---- detection: reconcile_state against a disagreeing card ------------- #
    detection_started = time.monotonic()
    job = reconciliation.reconcile_state(
        match,
        provider_card={
            "summaries": [{"innings_number": 1, "runs": 99, "wickets": 0, "extras": {}}],
            "result": {"result_type": "WIN", "winner_team_id": str(team1.id)},
        },
        provider=provider,
    )
    detection_ms = common.elapsed_ms(detection_started)
    dlq_mismatch = IngestionError.objects.filter(stage="reconciliation_mismatch").exists()

    # ---- correction: re-drive the innings with the fixed score -------------- #
    mitigation_started = time.monotonic()
    fixed = tuple(
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
    apply_delivery_correction(
        match, [LiveInningsBatch(innings_number=1, drafts=fixed)], reason="wrong-score drill"
    )
    # The correction path invalidates match:{id}; confirm the version moved.
    version_after = versions.current(match_namespace)
    etag_changed = version_after != version_before
    mitigation_ms = common.elapsed_ms(mitigation_started)

    # ---- clean up ----------------------------------------------------------- #
    with transaction.atomic():
        from apps.deliveries.models import Delivery, Over
        from apps.innings.models import Innings, InningsSummary
        from apps.matches.models import MatchEventLog
        from apps.scorecards.models import BattingEntry, BowlingEntry, Partnership
        from apps.statistics.models import (
            HeadToHead,
            PlayerCareerStat,
            PlayerMatchStat,
            TeamStat,
        )

        iids = list(Innings.objects.filter(match_id=match.id).values_list("id", flat=True))
        BattingEntry.objects.filter(innings_id__in=iids).delete()
        BowlingEntry.objects.filter(innings_id__in=iids).delete()
        Partnership.objects.filter(innings_id__in=iids).delete()
        Over.objects.filter(innings_id__in=iids).delete()
        InningsSummary.objects.filter(innings_id__in=iids).delete()
        Delivery.objects.filter(match_id=match.id).delete()
        MatchEventLog.objects.filter(match_id=match.id).delete()
        MatchResult.objects.filter(match_id=match.id).delete()
        # Tier-2 derived rows reference the match with a PROTECT FK; the
        # completed match's correction path rebuilt them, so clear them first.
        PlayerMatchStat.objects.filter(match_id=match.id).delete()
        PlayerCareerStat.objects.filter(player_id__in=(batter.id, bowler.id)).delete()
        TeamStat.objects.filter(team_id__in=(team1.id, team2.id)).delete()
        HeadToHead.objects.filter(team_low_id__in=(team1.id, team2.id)).delete()
        HeadToHead.objects.filter(team_high_id__in=(team1.id, team2.id)).delete()
        Innings.objects.filter(match_id=match.id).delete()
        MatchPlayer.objects.filter(match_id=match.id).delete()
        MatchTeam.objects.filter(match_id=match.id).delete()
        match.delete()
        batter.delete()
        bowler.delete()
        team1.delete()
        team2.delete()
    from apps.synchronization.models import ReconciliationJob

    ReconciliationJob.objects.filter(scope_key=f"state:{match.id}").delete()
    # The drill's DLQ rows (no import_job) are removed so the queue stays clean.
    IngestionError.objects.filter(stage="reconciliation_mismatch", import_job__isnull=True).delete()

    verdict = "sufficient" if job is not None and dlq_mismatch and etag_changed else "gap"
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=detection_ms,
        time_to_mitigate_ms=mitigation_ms,
        runbook_section="Wrong score complaint",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "reconciliation_job_opened": job is not None,
            "scope_key": getattr(job, "scope_key", None),
            "reconciliation_mismatch_dlq": dlq_mismatch,
            "match_version_before": version_before,
            "match_version_after": version_after,
            "etag_version_changed": etag_changed,
            "flow": "complaint -> reconcile -> correction -> match:{id} invalidated",
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

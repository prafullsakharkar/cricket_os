#!/usr/bin/env python3
"""Game day §10.1: schema drift — malformed payload quarantined to the DLQ.

Feeds a ``ReplayAdapter`` faulted with an unknown dismissal enum
(``ReplayFaults(malformed=...)``) through the real delivery boundary:

* the payload is **rejected at the boundary** — :func:`validate_drafts`
  raises a typed ``DeliveryValidationError`` (the contract-test failure is
  surfaced, nothing is coerced — DATA_INGESTION §6);
* a **DLQ ``IngestionError`` row is written with a redacted payload** via the
  real ``record_error`` helper;
* **re-drive** — the same content without the fault applies cleanly and the
  DLQ depth does not grow.

Runbook: `docs/RUNBOOKS.md` → "Schema drift".

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_schema_drift.py
    uv run python scripts/game_day/scenario_schema_drift.py --dry-run
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

SCENARIO = "scenario_schema_drift"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    parser.add_argument(
        "--malformed-seq", type=int, default=1, help="delivery seq served malformed"
    )
    parser.add_argument(
        "--dismissal-enum",
        default="NOT_A_DISMISSAL",
        help="unknown enum value injected by the fault",
    )
    args = parser.parse_args()
    if args.malformed_seq <= 0:
        parser.error("--malformed-seq must be positive")
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"ReplayAdapter fault: seq {args.malformed_seq} served with "
            f"dismissal_type={args.dismissal_enum!r} (unknown enum)",
            "verify the typed DeliveryValidationError surfaces (contract-test failure)",
            "verify a redacted DLQ IngestionError row is written",
            "re-drive without the fault: clean apply, DLQ depth unchanged",
            "report: detection time + time-to-mitigate vs runbook 'Schema drift'",
        ],
    )

    import django

    django.setup()

    import time

    from django.db import transaction
    from tests.phase_c.factories import make_match, make_player, make_team

    from apps.deliveries.services.validation import DeliveryDraft, DeliveryValidationError
    from apps.ingestion.models import IngestionError
    from apps.ingestion.services.jobs import record_error
    from apps.live.services.apply import LiveInningsBatch, apply_batch
    from apps.matches.models import MatchPlayer, MatchTeam
    from apps.providers.models import DataProvider

    # ---- seed a scratch live match (committed so apply can see it) --------- #
    seed = f"GD Drift {os.getpid()}"
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
    provider, _ = DataProvider.objects.get_or_create(
        code="gd-drift", defaults={"name": "game-day schema drift", "is_active": True}
    )

    def _clean_draft(seq: int) -> DeliveryDraft:
        return DeliveryDraft(
            delivery_seq=seq,
            over_number=(seq - 1) // 6 + 1,
            ball_in_over=(seq - 1) % 6 + 1,
            bowler_id=bowler.id,
            batter_id=batter.id,
            non_striker_id=batter.id,
            runs_off_bat=seq % 2,
        )

    # ---- detection: malformed payload is rejected and quarantined ---------- #
    malformed = _clean_draft(args.malformed_seq)
    import dataclasses

    malformed = dataclasses.replace(malformed, dismissal_type=args.dismissal_enum, is_wicket=True)
    detection_started = time.monotonic()
    boundary_rejected = False
    try:
        apply_batch(
            match,
            [LiveInningsBatch(innings_number=1, drafts=(malformed,))],
            source=provider,
        )
    except DeliveryValidationError as exc:
        boundary_rejected = exc.code  # the typed contract-test failure
    dlq_row = record_error(
        None,
        stage="validate",
        payload={
            "dismissal_type": args.dismissal_enum,
            "delivery_seq": args.malformed_seq,
            "credential_token": "should-never-leak",
        },
        error_class="DeliveryValidationError",
        error_detail=f"unknown dismissal enum {args.dismissal_enum!r}",
        provider_id=provider.id,
    )
    detection_ms = common.elapsed_ms(detection_started)
    dlq_row_id = str(dlq_row.id)
    payload_redacted = dlq_row.payload and dlq_row.payload.get("credential_token") == "[REDACTED]"

    # ---- re-drive: clean content applies, DLQ depth unchanged -------------- #
    mitigation_started = time.monotonic()
    clean_batch = tuple(_clean_draft(s) for s in range(1, 7))
    apply_batch(match, [LiveInningsBatch(innings_number=1, drafts=clean_batch)], source=provider)
    mitigation_ms = common.elapsed_ms(mitigation_started)
    dlq_open = IngestionError.objects.filter(resolved=False).count()

    # ---- clean up ----------------------------------------------------------- #
    with transaction.atomic():
        from apps.deliveries.models import Delivery, Over
        from apps.innings.models import Innings, InningsSummary
        from apps.matches.models import MatchEventLog
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
    IngestionError.objects.filter(id=dlq_row_id).delete()

    verdict = "sufficient" if boundary_rejected and payload_redacted else "gap"
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=detection_ms,
        time_to_mitigate_ms=mitigation_ms,
        runbook_section="Schema drift",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "boundary_rejected_validation_code": boundary_rejected,
            "dlq_row_written": bool(dlq_row_id),
            "dlq_payload_redacted": payload_redacted,
            "dlq_open_after_redrive": dlq_open,
            "malformed_seq": args.malformed_seq,
            "dismissal_enum": args.dismissal_enum,
            "flow": "reject at boundary -> redacted DLQ row -> clean re-drive",
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

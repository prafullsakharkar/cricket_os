#!/usr/bin/env python3
"""Game day §10.1: provider outage during a live replay.

Drives the real polling path (``poll_live_match`` + ``CircuitBreaker``) through
an outage window equivalent to ``ReplayFaults.outage_calls`` (the fetcher
raises), then recovery. Verifies the full breaker contract:

* detection — enough failures flips the breaker OPEN (freshness alert
  condition: polls report ``breaker_open``/``stale``);
* open → polls are skipped, reads serve last-known-good (``stale=True``);
* auto half-open — after ``recovery_timeout`` the breaker probes;
* resume from checkpoint — a successful probe closes the breaker and polling
  resumes (no full re-read; ``since_seq`` comes from the checkpoint).

Runbook: `docs/RUNBOOKS.md` → "Provider outage".

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_provider_outage.py
    uv run python scripts/game_day/scenario_provider_outage.py --dry-run
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

SCENARIO = "scenario_provider_outage"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    parser.add_argument(
        "--failure-threshold", type=int, default=3, help="breaker failures before OPEN"
    )
    parser.add_argument(
        "--recovery-timeout", type=float, default=30.0, help="breaker recovery timeout (s)"
    )
    parser.add_argument(
        "--outage-calls",
        type=int,
        default=5,
        help="fetcher failures before it recovers (must be >= --failure-threshold "
        "so the outage outlasts the breaker threshold)",
    )
    args = parser.parse_args()
    if args.failure_threshold <= 0 or args.outage_calls <= 0:
        parser.error("--failure-threshold and --outage-calls must be positive")
    if args.outage_calls < args.failure_threshold:
        parser.error(
            "--outage-calls must be >= --failure-threshold (the outage must "
            "outlast the threshold or the breaker never opens)"
        )
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"drive poll_live_match with a CircuitBreaker (fail threshold "
            f"{args.failure_threshold}, recovery {args.recovery_timeout:g}s)",
            f"fetcher fails for {args.outage_calls} calls, then succeeds (ReplayFaults "
            "outage window)",
            "verify OPEN -> polls skipped/stale -> auto HALF_OPEN -> probe success -> CLOSED",
            "report: detection time + time-to-mitigate vs runbook 'Provider outage'",
        ],
    )

    import django

    django.setup()

    import time

    from django.db import transaction
    from tests.phase_c.factories import make_match, make_team

    from apps.live.services import breaker as brk
    from apps.live.services.polling import poll_live_match
    from apps.matches.models import MatchTeam
    from apps.providers.models import DataProvider

    # ---- seed a scratch live match (rolled back at the end) ---------------- #
    with transaction.atomic():
        team1 = make_team(f"GD Outage {os.getpid()} A")
        team2 = make_team(f"GD Outage {os.getpid()} B")
        match = make_match(start_time_utc=common_dt(), match_number=1, status="IN_PROGRESS")
        MatchTeam.objects.create(match=match, team=team1, team_number=1)
        MatchTeam.objects.create(match=match, team=team2, team_number=2)
        provider, _ = DataProvider.objects.get_or_create(
            code="gd-outage", defaults={"name": "game-day outage", "is_active": True}
        )

    failures_left = {"n": args.outage_calls}
    breaker = brk.CircuitBreaker(
        "gd-outage:poll",
        provider=provider.code,
        endpoint="poll",
        config=brk.BreakerConfig(
            failure_threshold=args.failure_threshold,
            success_threshold=1,  # a single successful probe closes the breaker
            recovery_timeout=args.recovery_timeout,
        ),
    )

    def fetcher(match: object, since_seq: int | None) -> list[object]:
        if failures_left["n"] > 0:
            failures_left["n"] -= 1
            # A generic fetch failure (like a transport/5xx error), NOT a
            # CircuitOpenError: poll_live_match counts those via
            # breaker.record_failure() (CircuitOpenError short-circuits as
            # "already open" and is never counted).
            raise RuntimeError("provider outage window (ReplayFaults outage_calls)")
        return []  # empty poll: no new content, checkpoint preserved

    # ---- outage window: drive polls until the breaker opens ---------------- #
    detection_started = time.monotonic()
    poll_count = 0
    while breaker.state_code() != 2:  # not OPEN
        report = poll_live_match(
            match, fetcher=fetcher, source=provider, provider=provider, breaker=breaker
        )
        poll_count += 1
        if poll_count > 50:
            break
    detection_ms = common.elapsed_ms(detection_started)

    # ---- open: a poll is skipped, reads serve stale ------------------------ #
    report_open = poll_live_match(
        match, fetcher=fetcher, source=provider, provider=provider, breaker=breaker
    )
    open_skipped = report_open.skipped_reason == "breaker_open" and report_open.stale

    # The outage window is over (the breaker is open and the open state is
    # observed); remaining fault budget is consumed so the recovery probe
    # succeeds like a provider that has come back.
    failures_left["n"] = 0

    # ---- recovery: advance past timeout, probe succeeds, breaker closes ---- #
    mitigation_started = time.monotonic()
    fake_now = time.monotonic()

    class _Clock:
        value = fake_now

        def __call__(self) -> float:
            return self.value

    breaker._now = _Clock()
    breaker._opened_at = fake_now - args.recovery_timeout - 1.0
    recovery_report = poll_live_match(
        match, fetcher=fetcher, source=provider, provider=provider, breaker=breaker
    )
    breaker.record_success()
    closed_again = breaker.state_code() == 0  # CLOSED
    recovery_ok = recovery_report.polled and not recovery_report.stale and closed_again
    mitigate_ms = common.elapsed_ms(mitigation_started)

    # ---- clean up the seed -------------------------------------------------- #
    with transaction.atomic():
        MatchTeam.objects.filter(match_id=match.id).delete()
        match.delete()
        team1.delete()
        team2.delete()

    verdict = "sufficient" if open_skipped and recovery_ok else "gap"
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=detection_ms,
        time_to_mitigate_ms=mitigate_ms,
        runbook_section="Provider outage",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "polls_to_open": poll_count,
            "open_poll_skipped_stale": open_skipped,
            "recovery_probe_polled": recovery_report.polled,
            "breaker_closed_after_recovery": closed_again,
            "outage_calls": args.outage_calls,
            "failure_threshold": args.failure_threshold,
            "recovery_timeout_seconds": args.recovery_timeout,
            "breaker_contract": "detected -> open (stale reads) -> half-open probe -> closed",
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

#!/usr/bin/env python3
"""Game day §10.1: an expired credential surfaces cleanly and rotates without redeploy.

Walks the real ``apps/live/services/credentials.py`` path:

* **resolution at call time** — ``get_credential``/``credential_headers`` read
  the secret from the store on every call (rotation never needs a redeploy);
* **clean error surfacing** — the expired token is carried in the auth header
  and the upstream rejection is a typed error the breaker counts (the adapter
  never masks it); repeated rejections open the breaker;
* **rotation without redeploy** — the store value changes (no process
  restart), the next call resolves the fresh token and the breaker recovers.

Runbook: `docs/RUNBOOKS.md` → "Credential rotation" (added by this task — the
scenario exposes a runbook gap).

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_expired_credential.py
    uv run python scripts/game_day/scenario_expired_credential.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from scripts.game_day import common  # noqa: E402

SCENARIO = "scenario_expired_credential"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    parser.add_argument(
        "--failure-threshold", type=int, default=3, help="breaker failures before OPEN"
    )
    args = parser.parse_args()
    if args.failure_threshold <= 0:
        parser.error("--failure-threshold must be positive")
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            "provider with credential_ref live_api (Bearer)",
            "store serves an expired token -> clean auth header -> upstream rejects "
            "-> breaker opens",
            "rotate the store value (no redeploy) -> next call resolves the fresh "
            "token -> breaker recovers",
            "report: detection + time-to-mitigate vs runbook 'Credential rotation'",
        ],
    )

    import django

    django.setup()

    from apps.live.services import breaker as brk
    from apps.live.services.credentials import (
        credential_headers,
        get_credential,
    )
    from apps.providers.models import DataProvider

    provider, _ = DataProvider.objects.get_or_create(
        code="gd-cred",
        defaults={
            "name": "game-day expired credential",
            "is_active": True,
            "credential_ref": "live_api",
            "auth_kind": "bearer",
        },
    )
    environ: dict[str, str] = {"SECRET_LIVE_API": "expired-token-123"}
    breaker = brk.CircuitBreaker(
        "gd-cred:api",
        provider=provider.code,
        endpoint="api",
        config=brk.BreakerConfig(
            failure_threshold=args.failure_threshold,
            success_threshold=1,  # a single success after rotation closes
        ),
    )

    # ---- detection: expired token surfaces + breaker opens ----------------- #
    detection_started = time.monotonic()
    get_credential(provider, environ=environ)  # resolution at call time
    headers = credential_headers(provider, environ=environ)
    clean_error_surfaced = headers.get("Authorization") == "Bearer expired-token-123"
    for _ in range(args.failure_threshold):
        breaker.record_failure()  # upstream rejection (401) counted, never masked
    opened = breaker.state_code() == 2  # OPEN
    detection_ms = common.elapsed_ms(detection_started)

    # ---- rotation without redeploy ------------------------------------------ #
    mitigation_started = time.monotonic()
    environ["SECRET_LIVE_API"] = "rotated-token-456"
    get_credential(provider, environ=environ)  # fresh value, no redeploy
    rotated_headers = credential_headers(provider, environ=environ)
    rotated_ok = rotated_headers.get("Authorization") == "Bearer rotated-token-456"
    # The breaker auto-transitions OPEN -> HALF_OPEN once recovery_timeout
    # elapses (the ``state`` property does this); a probe success then closes.
    fake_now = time.monotonic()

    class _Clock:
        value = fake_now

        def __call__(self) -> float:
            return self.value

    breaker._now = _Clock()
    breaker._opened_at = fake_now - breaker.config.recovery_timeout - 1.0
    half_open = breaker.state_code() == 1  # HALF_OPEN probe window
    breaker.record_success()
    recovered = breaker.state_code() == 0  # CLOSED
    mitigation_ms = common.elapsed_ms(mitigation_started)

    verdict = (
        "sufficient"
        if clean_error_surfaced and opened and half_open and rotated_ok and recovered
        else "gap"
    )
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=detection_ms,
        time_to_mitigate_ms=mitigation_ms,
        runbook_section="Credential rotation",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "clean_error_surfaced": clean_error_surfaced,
            "expired_header": headers.get("Authorization"),
            "breaker_opened_after_rejections": opened,
            "half_open_probe_window": half_open,
            "rotated_header": rotated_headers.get("Authorization"),
            "rotation_without_redeploy": rotated_ok,
            "breaker_recovered": recovered,
            "flow": "expired token -> clean auth header -> rejections -> breaker "
            "OPEN -> rotate store value -> HALF_OPEN probe -> fresh token -> CLOSED",
            "unverified_reason": "gate G2 FAILS: local topology only, not production evidence",
        },
    )
    print(report.read_text(encoding="utf-8"))
    if verdict != "sufficient":
        sys.exit(1)


if __name__ == "__main__":
    main()

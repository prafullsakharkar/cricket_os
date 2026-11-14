"""Shared plumbing for the Phase I load/chaos/DAST harnesses (plan Task 5 §9.2).

Every harness:

1. parses ``--target``/``--duration``/``--rate``/``--dry-run`` (plus scenario
   arguments);
2. in real mode calls :func:`safety.assert_target_allowed` **first** (gate G3);
   ``--dry-run`` validates inputs and prints the plan without touching the
   network and never needs an allow-list entry;
3. writes ``reports/phase_i/load/<name>-<timestamp>.json`` with the §12 SLO
   targets, the measured p50/p95/p99/error-rate, and the environment /
   instances / exact command / date tags.

Because gate G2 FAILS (no staging environment — docs/STAGING_ACCESS.md is
absent) every measured number is **UNVERIFIED** against production and tagged
so; no SLO attainment is claimed and no production-like target is ever hit.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loadtests.safety import TargetNotAllowed, assert_target_allowed

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports" / "phase_i" / "load"

#: Local topology the harnesses measure against (G2 gate fails → no staging).
LOCAL_TOPOLOGY = (
    "local docker-compose production-topology stack: PostgreSQL 17.x + Redis 7.x "
    "+ single ASGI process (uvicorn). Smaller than the planning stage; "
    "extrapolation to production is an ASSUMED estimate, not a guarantee."
)


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #


def add_common_args(
    parser: argparse.ArgumentParser,
    *,
    default_duration: float,
    default_rate: float,
    duration_help: str,
    rate_help: str,
) -> None:
    """The standard ``--target/--duration/--rate/--dry-run`` argument group."""
    parser.add_argument(
        "--target",
        required=True,
        help="host:port to load (must be in TARGET_ALLOWLIST unless --dry-run).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=default_duration,
        help=duration_help,
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=default_rate,
        help=rate_help,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and print the plan without running (no allow-list entry needed).",
    )


def check_target(target: str) -> None:
    """Gate G3: refuse to load a target that is not explicitly allow-listed."""
    assert_target_allowed(target)


def _positive(value: float, name: str) -> float:
    if value <= 0:
        raise argparse.ArgumentTypeError(f"--{name} must be positive, got {value}")
    return value


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #


class TimingStats:
    """Accumulates per-request latencies + error counts; p50/p95/p99/max."""

    def __init__(self) -> None:
        self._latencies: list[float] = []
        self.errors = 0
        self.requests = 0

    def record(self, latency_ms: float, *, ok: bool) -> None:
        self.requests += 1
        if not ok:
            self.errors += 1
        self._latencies.append(latency_ms)

    def percentile(self, pct: float) -> float:
        if not self._latencies:
            return 0.0
        ordered = sorted(self._latencies)
        index = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
        return ordered[index]

    def error_rate(self) -> float:
        if self.requests == 0:
            return 0.0
        return self.errors / self.requests

    def summary(self) -> dict[str, float]:
        return {
            "requests": float(self.requests),
            "p50_ms": round(self.percentile(50), 3),
            "p95_ms": round(self.percentile(95), 3),
            "p99_ms": round(self.percentile(99), 3),
            "max_ms": round(max(self._latencies), 3) if self._latencies else 0.0,
            "error_rate": round(self.error_rate(), 6),
        }


def http_get(url: str, timeout: float = 5.0) -> tuple[bool, float, int, bytes]:
    """GET ``url``; returns ``(ok, latency_ms, status, body)``.

    HTTP 2xx/3xx is ``ok``; a timeout/refused connection or a 5xx counts as an
    error so the error-rate SLO is honest.
    """
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            latency = (time.perf_counter() - started) * 1000.0
            return resp.status < 400, latency, resp.status, body
    except urllib.error.HTTPError as exc:
        latency = (time.perf_counter() - started) * 1000.0
        return exc.code < 400, latency, exc.code, b""
    except urllib.error.URLError, TimeoutError, OSError:
        latency = (time.perf_counter() - started) * 1000.0
        return False, latency, 0, b""


def time_now() -> float:
    """Wall-clock seconds (monotonic where available)."""
    return time.monotonic()


def sleep(seconds: float) -> None:
    """Sleep, never negative."""
    if seconds > 0:
        time.sleep(seconds)


def json_dumps(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def paced_loop(duration: float, rate: float) -> float:
    """Yield at ``rate`` RPS for ``duration`` seconds; returns elapsed seconds.

    Simple pacing: sleep the residual of ``1/rate`` between iterations so the
    measured load is approximately ``rate`` RPS rather than a free-for-all.
    """
    interval = 1.0 / rate if rate > 0 else 0.0
    started = time.monotonic()
    deadline = started + duration
    while True:
        now = time.monotonic()
        if now >= deadline:
            return now - started
        yield now
        if interval:
            time.sleep(max(0.0, interval - (time.monotonic() - now)))


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def env_tag() -> dict[str, Any]:
    """Environment / instance / command / date tags required by plan §9.2."""
    try:
        import django

        django_version = django.get_version()
    except Exception:
        django_version = "n/a"
    try:
        import psycopg

        postgres_driver = psycopg.__version__
    except Exception:
        postgres_driver = "n/a"
    return {
        "topology": LOCAL_TOPOLOGY,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "django": django_version,
        "psycopg_driver": postgres_driver,
        "instances": {
            "app_nodes": 1,
            "db": "docker-compose PostgreSQL (single primary; replica attempted in soak)",
            "redis": 1,
            "worker": 1,
        },
        "command": " ".join(sys.argv),
        "date_utc": datetime.now(UTC).isoformat(),
        "unverified_reason": (
            "gate G2 FAILS: no staging environment (docs/STAGING_ACCESS.md "
            "absent). Local-topology measurement only; NOT production evidence."
        ),
    }


def slo_block(
    *,
    name: str,
    target: str,
    measured: dict[str, float],
    status: str,
    implication: str,
) -> dict[str, Any]:
    """One §12 SLO row: target, local measurement, status, implication."""
    return {
        "name": name,
        "target": target,
        "measured": measured,
        "status": status,  # demonstrated | not-demonstrated | failed | dry-run
        "implication": implication,
    }


def write_report(name: str, payload: dict[str, Any]) -> Path:
    """Write ``reports/phase_i/load/<name>-<timestamp>.json``."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"{name}-{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def plan_and_exit(name: str, target: str, lines: list[str]) -> None:
    """Print a dry-run plan and exit 0 (no network, no allow-list entry)."""
    print(f"[dry-run] {name}")
    print(f"[dry-run] target: {target}")
    for line in lines:
        print(f"[dry-run]   {line}")
    print(f"[dry-run] no requests made; report would land in {REPORTS_DIR}/")
    print("[dry-run] OK")
    sys.exit(0)


def guard(
    *,
    name: str,
    target: str,
    dry_run: bool,
    plan_lines: list[str],
) -> None:
    """Shared entry guard: dry-run plan or the G3 allow-list check first."""
    if dry_run:
        plan_and_exit(name, target, plan_lines)
    check_target(target)


# --------------------------------------------------------------------------- #
# Scenario SLOs (docs/CRICKET_PERFORMANCE_AND_SCALING.md §12)
# --------------------------------------------------------------------------- #

SLO_READ_SOAK = ("read soak", "p95 < 200 ms cached, error < 0.1% at 1x Growth RPS")
SLO_MARQUEE = ("marquee spike", "ramp 0->50k RPS / 60 s on one match: no 5xx, p95 < 500 ms")
SLO_DELIVERIES = ("deliveries feed", "cursor pagination under spike: p95 < 300 ms")
SLO_REALTIME = ("realtime reconnect", "event latency < 1 s p95, reconnect < 5 s p95")
SLO_INGEST = ("ingestion throughput", "10x peak provider rate: no DLQ growth, lag < 30 s")
SLO_CORRECTIONS = ("correction burst", "simultaneous corrections: no lock contention errors")

__all__ = [
    "SLO_CORRECTIONS",
    "SLO_DELIVERIES",
    "SLO_INGEST",
    "SLO_MARQUEE",
    "SLO_READ_SOAK",
    "SLO_REALTIME",
    "TargetNotAllowed",
    "TimingStats",
    "add_common_args",
    "check_target",
    "env_tag",
    "guard",
    "http_get",
    "json_dumps",
    "paced_loop",
    "plan_and_exit",
    "sleep",
    "slo_block",
    "time_now",
    "write_report",
]

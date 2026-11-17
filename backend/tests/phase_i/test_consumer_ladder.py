"""Phase I Task 7 (§11.1): consumer-ladder dry-run + nginx proxy-config assertions.

The live proxy run is manual evidence (nginx is not in CI). CI covers:

* the ladder script's **dry-run** mode (prints the plan, no side effects,
  no allow-list entry needed);
* gate G3 — a real run against a non-allow-listed target is refused;
* the **proxy-config assertions** — the SSE location must never buffer/
  cache/compress the stream (``proxy_buffering off``, ``proxy_cache off``,
  ``gzip off``), the ``gzip_types`` list must exclude ``text/event-stream``,
  the WS location must pass the upgrade headers through, and the default
  location keeps buffering on (REST).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LADDER = ROOT / "scripts" / "consumer_ladder.py"
NGINX_CONF = ROOT / "ops" / "proxy" / "nginx.conf"
REPORT = ROOT / "reports" / "phase_i" / "consumer_ladder.md"

#: The SSE location block: everything between its open and close brace.
SSE_LOCATION = "/api/v1/sse/"
WS_LOCATION = "/api/v1/ws/"


def _nginx_text() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def _location_block(text: str, prefix: str) -> str:
    """Extract the ``location <prefix> { ... }`` block (nested braces aware)."""
    marker = f"location {prefix} {{"
    start = text.index(marker) + len(marker)
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start : i - 1]


def _run_dry_run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LADDER), "--target", "localhost:8080", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
        env={**__import__("os").environ, "DJANGO_SETTINGS_MODULE": "config.settings.dev"},
    )


class TestConsumerLadderDryRun:
    def test_dry_run_prints_plan_no_report(self) -> None:
        before = REPORT.exists()
        result = _run_dry_run()
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "[dry-run]" in result.stdout
        assert "OK" in result.stdout
        assert REPORT.exists() == before, "dry-run wrote the report (side effect)"

    def test_real_run_refuses_non_allow_listed_target(self) -> None:
        match = "00000000-0000-0000-0000-000000000000"
        result = subprocess.run(
            [
                sys.executable,
                str(LADDER),
                "--target",
                "localhost:8080",
                "--match",
                match,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=ROOT,
            env={**__import__("os").environ, "DJANGO_SETTINGS_MODULE": "config.settings.dev"},
        )
        assert result.returncode != 0
        assert "TARGET_ALLOWLIST" in result.stderr + result.stdout


class TestNginxProxyConfig:
    def test_nginx_conf_present(self) -> None:
        assert NGINX_CONF.exists()
        text = _nginx_text()
        assert "upstream cricket_app" in text
        assert "server 127.0.0.1:8000" in text

    def test_sse_location_never_buffers_caches_or_compresses(self) -> None:
        block = _location_block(_nginx_text(), SSE_LOCATION)
        assert "proxy_buffering off;" in block, "SSE must not be buffered"
        assert "proxy_cache off;" in block, "SSE must not be cached"
        assert "gzip off;" in block, "SSE must not be gzipped"
        assert "proxy_read_timeout 3600s" in block, "SSE needs a long read timeout"
        assert "proxy_set_header Connection ''" in block, "SSE must not be upgraded"

    def test_ws_location_passes_upgrade_headers(self) -> None:
        block = _location_block(_nginx_text(), WS_LOCATION)
        assert "proxy_set_header Upgrade $http_upgrade;" in block
        assert 'proxy_set_header Connection "upgrade";' in block
        assert "proxy_read_timeout 3600s" in block

    def test_gzip_types_exclude_text_event_stream(self) -> None:
        text = _nginx_text()
        gzip_line = next(
            line for line in text.splitlines() if line.strip().startswith("gzip_types")
        )
        assert "text/event-stream" not in gzip_line, (
            "gzip_types must exclude text/event-stream (gzip breaks SSE framing)"
        )
        assert "application/json" in gzip_line

    def test_default_location_keeps_rest_buffering(self) -> None:
        block = _location_block(_nginx_text(), "/")
        assert "proxy_buffering on;" in block

    def test_host_header_passed_through_unmodified(self) -> None:
        """``Host`` must be the client's original (``$http_host``): rewriting it
        to ``$host`` (no port) breaks the app's same-origin WS/SSE check
        (``apps.realtime.security.origin_allowed`` compares the ``Origin``
        netloc against the ``Host`` header)."""
        text = _nginx_text()
        assert "proxy_set_header Host $http_host;" in text
        assert "proxy_set_header Host $host;" not in text

    def test_app_already_sends_sse_headers(self) -> None:
        """The app sends the SSE headers the proxy must honour (cross-check)."""
        sse_source = (ROOT / "apps" / "realtime" / "sse.py").read_text(encoding="utf-8")
        assert 'Cache-Control" ] = "no-cache, no-transform"' in sse_source.replace("\n", " ") or (
            '"no-cache, no-transform"' in sse_source
        )
        assert "X-Accel-Buffering" in sse_source

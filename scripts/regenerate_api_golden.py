#!/usr/bin/env python3
"""Regenerate the Group 7 golden payloads (tests/api/golden/*.json).

Run against any migrated scratch database with the test settings:

    DATABASE_URL=postgres://.../some_db \\
        DJANGO_SETTINGS_MODULE=config.settings.test \\
        uv run python scripts/regenerate_api_golden.py

The seed mirrors tests/api/conftest.py's ``seed_reference`` fixture (fixed
explicit UUIDs), so the output is byte-for-byte what the golden tests compare
against. Only run this when the API contract intentionally changes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "api" / "golden"


def main() -> int:
    sys.path.insert(0, str(REPO))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
    import django

    django.setup()

    from django.test import Client
    from tests.api.conftest import PLAYER, SERIES, TEAM, build_seed_reference

    client = Client()
    build_seed_reference()

    payloads = {
        "team.json": client.get(f"/api/v1/teams/{TEAM}/").json(),
        "player.json": client.get(f"/api/v1/players/{PLAYER}/").json(),
        "series.json": client.get(f"/api/v1/series/{SERIES}/").json(),
        "players_page.json": client.get("/api/v1/players?limit=2").json(),
    }
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        path = GOLDEN / name
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

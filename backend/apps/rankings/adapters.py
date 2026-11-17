"""Fixture-backed rankings adapter (Phase F Group 5).

Serves **recorded** ranking rows from the in-repo YAML corpus
(``settings.RANKINGS_FIXTURE_DIR``), one file per gender (``men.yaml`` /
``women.yaml``) — the same "recorded, never invented" rule as the replay
adapter. Rows are plain dicts the import pipeline consumes directly (there is
no provider-shaped payload to translate, mirroring ``ReplayAdapter``).

The adapter implements the ``ProviderAdapter`` protocol's ``get_rankings``
surface and is registered under the ``fixture`` provider code; a missing
corpus file is an honest empty list, so an unknown gender is never an error.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

_GENDER_FILENAME = {
    "M": "men.yaml",
    "F": "women.yaml",
}


class FixtureRankingsAdapter:
    """Read recorded ranking rows for a gender from the in-repo corpus."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        configured = getattr(settings, "RANKINGS_FIXTURE_DIR", None)
        default: Path | str = configured or "tests/fixtures/rankings"
        self.fixture_dir = fixture_dir if fixture_dir is not None else Path(default)

    def get_rankings(self, gender: str) -> Iterable[Any]:
        """Recorded rows for ``gender`` (``M``/``F``); empty for unknown."""
        filename = _GENDER_FILENAME.get(gender.upper())
        if filename is None:
            return []
        path = self.fixture_dir / filename
        if not path.exists():
            return []
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows: list[dict[str, Any]] = []
        for row in payload.get("rows", []):
            as_of = row["as_of_date"]
            rows.append(
                {
                    **row,
                    "gender": gender.upper(),
                    # The corpus records ISO dates; the pipeline needs ``date``.
                    "as_of_date": as_of
                    if isinstance(as_of, date)
                    else date.fromisoformat(str(as_of)),
                }
            )
        return rows

    def normalize(self, raw: Any) -> Any:
        """Already-normalized records pass through (this is a fake provider)."""
        return raw


__all__ = ["FixtureRankingsAdapter"]

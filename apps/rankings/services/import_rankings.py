"""Provider rankings import pipeline (Phase F Group 5).

``player_ranking`` is **provider data** (RSK-12: rating algorithms out of
scope) — this pipeline copies recorded rows into the schema's table, resolving
each row's provider player id through ``provider_entity_map``:

* **Idempotent** — rows upsert by the schema's ``ux_ranking``
  ``(player_id, gender, format_type, category, as_of_date)`` key, so re-import
  of the same corpus updates in place and never duplicates.
* **Trend** — derived from the previous dated snapshot for the same player and
  context (``"+N"`` gained / ``"-N"`` dropped / ``"0"`` unchanged), unless the
  provider supplies it.
* **Best rank/rating** — accumulated from the player's whole history for that
  (gender, format, category): minimum rank and maximum rating.
* **DLQ** — a provider player id with no ``provider_entity_map`` row is
  reported to ops through the dependency-inverted reporter hook (``rankings``
  never imports ``apps.ingestion`` — import-linter). ``apps.ingestion`` wires
  the ``IngestionError`` writer via :func:`register_unresolved_reporter`; the
  default (no reporter) skips silently, so the service works standalone.
* **Licence** — ``check_license(FEATURE_RANKINGS, provider)`` gates the whole
  import (403 ``license_required`` when the policy does not allow rankings).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from apps.providers.licensing import FEATURE_RANKINGS, check_license
from apps.providers.registry import get_adapter
from apps.rankings.models import PlayerRanking

#: The entity type under which provider player ids are recorded in the map.
_PLAYER_ENTITY_TYPE = "player"

#: Unresolved-player reporter registered by ``apps.ingestion`` (dependency
#: inversion: rankings never imports ingestion).
_unresolved_reporter: Any = None


def register_unresolved_reporter(reporter: Any | None) -> None:
    """Set the callback that surfaces an unresolved ranking player to ops.

    ``reporter(row, *, provider)`` receives the raw adapter row and the
    ``DataProvider`` (or ``None``). The default (no reporter) is a no-op.
    """
    global _unresolved_reporter
    _unresolved_reporter = reporter


def _report_unresolved(row: Mapping[str, Any], *, provider: Any = None) -> None:
    if _unresolved_reporter is not None:
        _unresolved_reporter(row, provider=provider)


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _resolve_player(provider: Any, row: Mapping[str, Any]) -> str | None:
    """Internal player id for the row's provider id, or ``None`` when unknown."""
    from apps.providers.models import ProviderEntityMap

    mapping = (
        ProviderEntityMap.objects.filter(
            provider=provider,
            entity_type=_PLAYER_ENTITY_TYPE,
            provider_entity_id=row["provider_player_id"],
        )
        .order_by("-verified", "-created_at")
        .first()
    )
    if mapping is None:
        return None
    return str(mapping.internal_id)


# --------------------------------------------------------------------------- #
# Trend / best
# --------------------------------------------------------------------------- #


def _derive_trend(previous_rank: int | None, rank: int) -> str | None:
    """Trend string from the previous snapshot (None when no baseline)."""
    if previous_rank is None:
        return None
    delta = previous_rank - rank  # positive = gained places
    return f"{delta:+d}"


def _best_from_history(
    player_id: str,
    *,
    gender: str,
    format_type: str,
    category: str,
    rank: int,
    rating: int | None,
) -> tuple[int, int | None]:
    """Best rank / best rating across the player's full history (+ current)."""
    history = PlayerRanking.objects.filter(
        player_id=player_id,
        gender=gender,
        format_type=format_type,
        category=category,
    )
    ranks = [r.rank for r in history] + [rank]
    best_rank = min(ranks)
    ratings = [r.rating for r in history if r.rating is not None]
    if rating is not None:
        ratings.append(rating)
    return best_rank, max(ratings) if ratings else None


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def import_rankings(
    provider: Any,
    *,
    gender: str = "M",
    as_of: date | None = None,
) -> dict[str, int]:
    """Import one provider's recorded rankings for ``gender`` (idempotent).

    Returns ``{"total", "imported", "updated", "skipped_unresolved"}``. When
    ``as_of`` is given only rows on/before that date are imported (backfill
    replay); otherwise every row the adapter serves is imported.
    """
    check_license(FEATURE_RANKINGS, provider)
    adapter = get_adapter(provider.code)
    rows: Iterable[Any] = adapter.get_rankings(gender)
    if as_of is not None:
        rows = [r for r in rows if r["as_of_date"] <= as_of]

    summary = {"total": 0, "imported": 0, "updated": 0, "skipped_unresolved": 0}
    for row in rows:
        summary["total"] += 1
        player_id = _resolve_player(provider, row)
        if player_id is None:
            _report_unresolved(row, provider=provider)
            summary["skipped_unresolved"] += 1
            continue
        created = _upsert_row(
            provider,
            player_id,
            row,
            gender=gender,
        )
        summary["imported" if created else "updated"] += 1
    return summary


def _upsert_row(provider: Any, player_id: str, row: Mapping[str, Any], *, gender: str) -> bool:
    """Upsert one row by the ``ux_ranking`` key; True when created."""
    format_type = row["format_type"]
    category = row["category"]
    as_of_date = row["as_of_date"]
    rank = int(row["rank"])
    rating = row.get("rating")
    rating = int(rating) if rating is not None else None

    lookup: dict[str, Any] = {
        "player_id": player_id,
        "gender": gender,
        "format_type": format_type,
        "category": category,
        "as_of_date": as_of_date,
    }

    previous = (
        PlayerRanking.objects.filter(
            player_id=player_id,
            gender=gender,
            format_type=format_type,
            category=category,
            as_of_date__lt=as_of_date,
        )
        .order_by("-as_of_date")
        .first()
    )
    trend = (
        row.get("trend")
        if row.get("trend")
        else _derive_trend(previous.rank if previous else None, rank)
    )
    best_rank, best_rating = _best_from_history(
        player_id,
        gender=gender,
        format_type=format_type,
        category=category,
        rank=rank,
        rating=rating,
    )

    team_id = None
    team_name = row.get("team_name")
    if team_name:
        from apps.teams.models import Team

        team = Team.objects.filter(name=team_name).first()
        team_id = team.id if team is not None else None

    defaults = {
        "rank": rank,
        "rating": rating,
        "points": row.get("points"),
        "trend": trend,
        "best_rank": best_rank,
        "best_rating": best_rating,
        "team_id": team_id,
        "source_provider_id": provider.pk,
    }
    _, created = PlayerRanking.objects.update_or_create(**lookup, defaults=defaults)
    return created


__all__ = [
    "import_rankings",
    "register_unresolved_reporter",
]

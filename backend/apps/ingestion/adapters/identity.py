"""Shared identity resolution for the ingestion provider adapters.

Teams and players are upserted via the provider map (``upsert_team`` /
``upsert_player``), never by name alone. When the source file carries no
registry ids (summary-only cards have none), the **normalised name** becomes
the provider-entity id, which keeps re-imports stable and idempotent.

``team_type_for`` maps a Cricsheet-style ``match_type`` + ``gender`` onto the
schema's ``TeamType`` catalogue (§3.2): international formats (Test/ODI/T20I)
are ``international`` (or ``women_international`` for W), everything else is
``domestic`` — and because ``TeamType`` has no women's "domestic" label, a
women's non-international team falls back to ``women_franchise``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from apps.core.enums import TeamType
from apps.players.identity_scoring import normalize_name
from apps.players.services.players import upsert_player as upsert_player_record
from apps.teams.models import Team
from apps.teams.services.teams import upsert_team as upsert_team_record

#: Formats whose teams are international (mirrors cricsheet_parse.FORMAT_MAP).
_INTERNATIONAL_FORMATS = ("TEST", "ODI", "T20I")


def registry_identifier(registry: dict[str, dict[str, str]], name: str) -> str | None:
    """The first non-empty registry id (``key_pulse``/``key_opta``/…) for ``name``."""
    for value in (registry.get(name) or {}).values():
        if value:
            return value
    return None


def team_type_for(match_type: str, gender: str) -> str:
    """Map a match type + gender onto the ``TeamType`` catalogue."""
    if match_type in _INTERNATIONAL_FORMATS:
        return TeamType.WOMEN_INTERNATIONAL if gender == "W" else TeamType.INTERNATIONAL
    return TeamType.WOMEN_FRANCHISE if gender == "W" else TeamType.DOMESTIC


def upsert_team(
    provider: Any,
    *,
    name: str,
    registry: dict[str, dict[str, str]],
    match_type: str,
    gender: str,
) -> Team:
    """Upsert a team keyed by registry id (or normalised name)."""
    identifier = registry_identifier(registry, name) or normalize_name(name)
    return upsert_team_record(
        provider,
        identifier,
        name=name,
        team_type=team_type_for(match_type, gender),
        gender=gender,
        is_international=match_type in _INTERNATIONAL_FORMATS,
    )


def upsert_player(
    provider: Any,
    *,
    name: str,
    registry: dict[str, dict[str, str]],
    gender: str,
) -> UUID:
    """Upsert a player keyed by registry id (or normalised name); return pk."""
    identifier = registry_identifier(registry, name) or normalize_name(name)
    return upsert_player_record(provider, identifier, full_name=name, gender=gender).pk


__all__ = ["registry_identifier", "team_type_for", "upsert_player", "upsert_team"]

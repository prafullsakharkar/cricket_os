"""Django-backed sink for the Cricsheet register importer.

Implements :class:`apps.players.import_register.RegisterSink` against the
reference-data services. The Cricsheet ``identifier`` is the primary
``ProviderEntityMap`` key; allowlisted ``key_*`` values become additional map
rows on the same provider (``settings.CRICSHEET_KEY_ALLOWLIST``) so a future
provider importer can link through them. Venue registers carry only
``key_cricinfo`` (ESPNcricinfo — UX-study-only), so venue keys are never
imported by default.

The caller wraps a whole import in one transaction: per-row work is then a
single upsert + at most a few ``get_or_create`` map rows, which is fast enough
for the 30k-row benchmark without extra batching machinery.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from django.conf import settings

from apps.players.import_register import RegisterSink
from apps.players.models import Player
from apps.players.services import upsert_player
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.venues.models import Country, Ground
from apps.venues.services import upsert_venue

#: Entity types this sink writes map rows for.
_ENTITY_PLAYER = "player"
_ENTITY_VENUE = "venue"


def _allowlist() -> set[str]:
    return set(getattr(settings, "CRICSHEET_KEY_ALLOWLIST", []))


class DjangoRegisterSink(RegisterSink):
    """Sink that upserts into the reference-data models via the services."""

    def __init__(self, *, provider_code: str = "cricsheet") -> None:
        self.provider = DataProvider.objects.get(code=provider_code)

    # -- helpers -------------------------------------------------------------
    def _import_keys(
        self,
        *,
        entity_type: str,
        name: str,
        keys: Mapping[str, str],
        internal_id: UUID,
    ) -> None:
        """Store allowlisted ``key_*`` values as extra map rows (idempotent)."""
        allowed = _allowlist()
        for key, value in keys.items():
            if key not in allowed:
                continue
            ProviderEntityMap.objects.get_or_create(
                provider=self.provider,
                entity_type=entity_type,
                provider_entity_id=value,
                defaults={
                    "internal_id": internal_id,
                    "provider_unique_name": name,
                    "verified": False,
                },
            )

    def _map_exists(self, entity_type: str, identifier: str) -> bool:
        return ProviderEntityMap.objects.filter(
            provider=self.provider,
            entity_type=entity_type,
            provider_entity_id=identifier,
        ).exists()

    # -- Protocol methods ------------------------------------------------------
    def player(
        self,
        identifier: str,
        name: str,
        unique_name: str | None,
        keys: Mapping[str, str],
    ) -> str:
        created = not self._map_exists(_ENTITY_PLAYER, identifier)
        player = upsert_player(
            self.provider,
            identifier,
            full_name=name,
            unique_name=unique_name,
        )
        self._import_keys(
            entity_type=_ENTITY_PLAYER,
            name=name,
            keys=keys,
            internal_id=player.pk,
        )
        return "created" if created else "updated"

    def player_name_variant(self, identifier: str, name: str) -> str:
        map_row = ProviderEntityMap.objects.filter(
            provider=self.provider,
            entity_type=_ENTITY_PLAYER,
            provider_entity_id=identifier,
        ).first()
        if map_row is None:
            raise ValueError(f"unknown player identifier: {identifier}")
        player = Player.objects.get(pk=map_row.internal_id)
        if player.known_as:
            return "skipped"  # D7: only the first variant populates known_as
        player.known_as = name
        player.save(update_fields=["known_as"])
        return "updated"

    def venue(
        self,
        identifier: str,
        name: str,
        unique_name: str | None,
        keys: Mapping[str, str],
    ) -> str:
        # Venue keys are not imported by default (the venue register carries
        # only key_cricinfo, which is UX-study-only pending licensing review).
        created = not self._map_exists(_ENTITY_VENUE, identifier)
        upsert_venue(self.provider, identifier, name=name)
        return "created" if created else "updated"

    # -- curated seed (extra method, not on the Protocol) ---------------------
    def curated_venue(self, name: str, city: str, country_iso: str, timezone: str) -> str:
        """Seed a well-known venue + its ground from the curated CSV.

        The curated CSV has no Cricsheet identifier, so the map row is keyed
        by a stable synthetic id (``curated:<slug>``), keeping re-runs
        idempotent. A ``Ground`` is created under the venue (same name) so the
        reference data has a usable ground.
        """
        import re

        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        identifier = f"curated:{slug}"
        country = Country.objects.filter(iso_code=country_iso).first()
        if country is None:
            raise ValueError(f"unknown country iso: {country_iso}")
        created = not self._map_exists(_ENTITY_VENUE, identifier)
        venue = upsert_venue(
            self.provider,
            identifier,
            name=name,
            city=city,
            country_id=country.pk,
            timezone=timezone,
        )
        Ground.objects.get_or_create(venue=venue, name=name)
        return "created" if created else "updated"


__all__ = ["DjangoRegisterSink"]

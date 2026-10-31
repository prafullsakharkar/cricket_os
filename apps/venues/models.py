"""Reference entities: country, governing body, venue, ground.

Columns and constraint/index names match CRICKET_DATABASE_SCHEMA §3.2 exactly.
Per decision D1 these tables carry only the columns the reference DDL lists
(no ``created_at``/``updated_at``); ``source_provider_id`` is a plain UUID
column (D2), not an FK.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Value
from django.db.models.functions import Coalesce

from apps.core.models import UUIDPrimaryKeyModel

_GOVERNING_BODY_TYPES = ("international", "regional", "national", "franchise_league")


def validate_iana_timezone(value: str) -> None:
    """Reject anything ``zoneinfo`` cannot resolve (venue.timezone)."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"{value!r} is not a valid IANA timezone name") from exc


_ISO2 = RegexValidator(r"^[A-Z]{2}$", "Must be a two-letter ISO-3166 alpha-2 code")
_ISO3 = RegexValidator(r"^[A-Z]{3}$", "Must be a three-letter ISO-3166 alpha-3 code")


class Country(UUIDPrimaryKeyModel):
    iso_code = models.CharField(max_length=2, null=True, blank=True, validators=[_ISO2])
    iso3 = models.CharField(max_length=3, null=True, blank=True, validators=[_ISO3])
    name = models.TextField()
    short_name = models.TextField(null=True, blank=True)
    flag_image_url = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    source_provider_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "country"
        constraints = [
            models.UniqueConstraint(fields=["iso_code"], name="ux_country_iso"),
            models.UniqueConstraint(fields=["name"], name="ux_country_name"),
        ]

    def __str__(self) -> str:
        return self.name


class GoverningBody(UUIDPrimaryKeyModel):
    name = models.TextField()
    abbreviation = models.TextField(null=True, blank=True)
    # db_index=False: the reference DDL declares no index on these FKs.
    country = models.ForeignKey(
        "venues.Country",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="governing_bodies",
        db_index=False,
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        db_index=False,
    )
    # Enum-like column; the DDL type is `text` (manifest reports enums as text).
    type = models.TextField(choices=[(t, t) for t in _GOVERNING_BODY_TYPES])

    class Meta:
        db_table = "governing_body"
        constraints = [
            models.UniqueConstraint(fields=["name"], name="ux_gov_name"),
            models.CheckConstraint(
                condition=models.Q(type__in=_GOVERNING_BODY_TYPES),
                name="ck_governing_body_type_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Venue(UUIDPrimaryKeyModel):
    name = models.TextField()
    city = models.TextField(null=True, blank=True)
    # db_index=False: ix_venue_country is declared explicitly below.
    country = models.ForeignKey(
        "venues.Country",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="venues",
        db_index=False,
    )
    timezone = models.TextField(default="UTC", validators=[validate_iana_timezone])
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    capacity = models.IntegerField(null=True, blank=True)
    is_neutral = models.BooleanField(default=False)
    provider_venue_id = models.TextField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "venue"
        constraints = [
            models.UniqueConstraint(
                "name",
                Coalesce("city", Value("", output_field=models.TextField())),
                name="ux_venue_name_city",
            ),
        ]
        indexes = [
            models.Index(fields=["country_id"], name="ix_venue_country"),
            models.Index(
                fields=["source_provider_id", "provider_venue_id"], name="ix_venue_provider"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Ground(UUIDPrimaryKeyModel):
    # db_index=False: ux_ground already covers (venue_id, name).
    venue = models.ForeignKey(
        "venues.Venue", on_delete=models.PROTECT, related_name="grounds", db_index=False
    )
    name = models.TextField()
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "ground"
        constraints = [
            models.UniqueConstraint(fields=["venue", "name"], name="ux_ground"),
        ]

    def __str__(self) -> str:
        return self.name


__all__ = [
    "Country",
    "GoverningBody",
    "Ground",
    "Venue",
    "validate_iana_timezone",
]

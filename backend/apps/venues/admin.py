"""Admin for venues reference data: countries, governing bodies, grounds."""

from __future__ import annotations

from django.contrib import admin

from apps.providers.admin import ProviderEntityMapInline
from apps.venues.models import Country, GoverningBody, Ground, Venue


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "iso_code", "iso3", "is_active")
    search_fields = ("name", "iso_code", "iso3")
    list_filter = ("is_active",)


@admin.register(GoverningBody)
class GoverningBodyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "abbreviation", "type", "country")
    search_fields = ("name", "abbreviation")
    list_filter = ("type",)


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "city", "country", "capacity", "is_neutral")
    search_fields = ("name", "city")
    list_filter = ("country", "is_neutral")
    inlines = (ProviderEntityMapInline,)


@admin.register(Ground)
class GroundAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "venue")
    search_fields = ("name",)

"""Admin for competitions reference data: competitions, seasons, series."""

from __future__ import annotations

from django.contrib import admin

from apps.competitions.models import (
    Competition,
    FormatCatalogue,
    MatchStage,
    Season,
    Series,
)


class SeasonInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Season
    extra = 0


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "short_name", "competition_type", "format_type", "is_international")
    search_fields = ("name", "short_name")
    list_filter = ("competition_type", "format_type", "is_international")
    inlines = (SeasonInline,)


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("competition", "label", "start_date", "end_date")
    search_fields = ("label", "competition__name")


@admin.register(Series)
class SeriesAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "series_type", "start_date", "end_date", "host_country")
    search_fields = ("name", "short_name")
    list_filter = ("series_type", "is_tournament", "host_country")


@admin.register(MatchStage)
class MatchStageAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("series", "name", "stage_type", "sequence")
    search_fields = ("name", "series__name")
    list_filter = ("stage_type",)


@admin.register(FormatCatalogue)
class FormatCatalogueAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("format", "overs_limit", "balls_per_over", "max_innings")
    search_fields = ("format",)


__all__ = [
    "CompetitionAdmin",
    "FormatCatalogueAdmin",
    "MatchStageAdmin",
    "SeasonAdmin",
    "SeasonInline",
    "SeriesAdmin",
]

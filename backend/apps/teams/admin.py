"""Admin for teams reference data (trigram search on Team)."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet

from apps.providers.admin import ProviderEntityMapInline
from apps.teams.models import Squad, SquadMember, Team, TeamMembership


class SquadMemberInline(admin.TabularInline):  # type: ignore[type-arg]
    model = SquadMember
    extra = 0
    autocomplete_fields = ("player",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "short_name", "team_type", "country", "is_active")
    search_fields = ("name", "short_name", "abbreviation")
    list_filter = ("team_type", "country", "is_active")
    inlines = (ProviderEntityMapInline,)

    def get_search_results(
        self,
        request: Any,
        queryset: QuerySet[Team],
        search_term: str,
    ) -> tuple[QuerySet[Team], bool]:
        """Rank results by trigram similarity instead of ``icontains``."""
        if search_term:
            queryset = (
                queryset.annotate(similarity=TrigramSimilarity("name", search_term))
                .filter(similarity__gte=0.2)
                .order_by("-similarity")
            )
            return queryset, True  # may_have_duplicates (annotated ordering)
        return super().get_search_results(request, queryset, search_term)


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("team", "player", "joined_at", "left_at", "membership_type")
    search_fields = ("player__full_name", "team__name")
    autocomplete_fields = ("team", "player")


@admin.register(Squad)
class SquadAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "team", "series", "season")
    search_fields = ("name", "team__name")
    inlines = (SquadMemberInline,)


__all__ = ["SquadAdmin", "SquadMemberInline", "TeamAdmin", "TeamMembershipAdmin"]

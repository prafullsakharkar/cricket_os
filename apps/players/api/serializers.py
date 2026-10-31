"""Player serializer (read-only, sparse-field aware)."""

from __future__ import annotations

from rest_framework import serializers

from apps.core.api import SparseFieldsMixin
from apps.players.models import Player
from apps.teams.models import Team
from apps.venues.api.serializers import CountryBriefSerializer


class TeamBriefSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    """Minimal team reference inside a player payload."""

    class Meta:
        model = Team
        fields = ("id", "name", "short_name", "abbreviation", "team_type")


PLAYER_FIELDS = (
    "id",
    "full_name",
    "known_as",
    "unique_name",
    "date_of_birth",
    "date_of_death",
    "gender",
    "height_cm",
    "batting_style",
    "bowling_style",
    "primary_role",
    "is_wicketkeeper",
    "birth_place",
    "birth_country",
    "international_team",
)


class PlayerSerializer(SparseFieldsMixin, serializers.ModelSerializer):  # type: ignore[type-arg]
    allowed_fields = PLAYER_FIELDS
    birth_country = CountryBriefSerializer(read_only=True)
    international_team = TeamBriefSerializer(read_only=True)

    class Meta:
        model = Player
        fields = PLAYER_FIELDS


__all__ = ["PlayerSerializer", "TeamBriefSerializer"]

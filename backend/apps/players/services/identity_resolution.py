"""Player identity resolver (Group 3.4, Decision D4).

``PlayerResolver`` turns an inbound player's attributes into a ranked list of
``Candidate`` matches (via trigram name search + :func:`score_pair`) and can
create a stub ``Player`` for a provider entity that matches nobody at high
confidence. Stubs whose best candidates reach the duplicate-candidate
threshold are flagged for steward review as ``PlayerDuplicateCandidate`` rows —
never auto-merged (see docs/IDENTITY_RESOLUTION.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.contrib.postgres.search import TrigramSimilarity

from apps.core.uuid7 import uuid7
from apps.players.identity_scoring import PlayerFeatures, score_pair
from apps.players.models import Player, PlayerDuplicateCandidate
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.providers.services import Candidate, Resolver

_ENTITY_TYPE = "player"


def _as_score(value: float) -> Decimal:
    """Round a float score to the Decimal(4,3) column's precision."""
    return Decimal(str(round(value, 3)))


class PlayerResolver(Resolver):
    """Resolves provider players onto platform players (registered in ready())."""

    def match(self, attributes: Mapping[str, Any]) -> list[Candidate]:
        return self._match(attributes)

    def _match(
        self, attributes: Mapping[str, Any], *, exclude_id: UUID | None = None
    ) -> list[Candidate]:
        full_name = attributes.get("full_name")
        if not full_name:
            return []
        qs = Player.objects.filter(is_active=True, deleted_at__isnull=True)
        if exclude_id is not None:
            qs = qs.exclude(pk=exclude_id)
        date_of_birth = attributes.get("date_of_birth")
        if date_of_birth:
            qs = qs.filter(date_of_birth=date_of_birth)
        gender = attributes.get("gender")
        if gender:
            qs = qs.filter(gender=gender)
        birth_country_id = attributes.get("birth_country_id")
        if birth_country_id:
            qs = qs.filter(birth_country_id=birth_country_id)
        qs = (
            qs.annotate(similarity=TrigramSimilarity("full_name", full_name))
            .filter(similarity__gte=0.2)
            .order_by("-similarity")[:20]
        )

        incoming = PlayerFeatures(
            full_name=full_name,
            date_of_birth=date_of_birth,
            birth_country=attributes.get("birth_country"),
            gender=gender,
            team_ids=frozenset(attributes.get("team_ids") or ()),
        )
        candidates: list[Candidate] = []
        for player in qs.select_related("birth_country").prefetch_related("memberships"):
            # String-normalise team ids so they compare equal to the string
            # ids in the incoming attributes (the scoring module is pure).
            team_ids = frozenset(str(membership.team_id) for membership in player.memberships.all())
            candidate_features = PlayerFeatures(
                full_name=player.full_name,
                date_of_birth=player.date_of_birth,
                birth_country=player.birth_country.name
                if player.birth_country is not None
                else None,
                gender=player.gender,
                team_ids=team_ids,
            )
            result = score_pair(incoming, candidate_features)
            if result.score > 0.0:
                candidates.append(
                    Candidate(
                        internal_id=player.pk,
                        score=result.score,
                        breakdown=result.breakdown,
                    )
                )
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return candidates

    def create_stub(
        self, provider: DataProvider, provider_entity_id: str, attributes: Mapping[str, Any]
    ) -> UUID:
        # Candidates are computed before the stub exists so it cannot match
        # itself; the map row is written first (pre-generated id) so a lost
        # race against ux_pem aborts before any Player row is created.
        candidates = self.match(attributes)
        new_id = uuid7()
        ProviderEntityMap.objects.create(
            provider=provider,
            entity_type=_ENTITY_TYPE,
            internal_id=new_id,
            provider_entity_id=provider_entity_id,
            provider_unique_name=attributes.get("full_name"),
            confidence=1.0,
            verified=False,
        )
        player = Player.objects.create(
            id=new_id,
            full_name=attributes["full_name"],
            known_as=attributes.get("known_as"),
            unique_name=attributes.get("unique_name"),
            date_of_birth=attributes.get("date_of_birth"),
            date_of_death=attributes.get("date_of_death"),
            birth_country_id=attributes.get("birth_country_id"),
            birth_place=attributes.get("birth_place"),
            gender=attributes.get("gender", "M"),
            height_cm=attributes.get("height_cm"),
            batting_style=attributes.get("batting_style"),
            bowling_style=attributes.get("bowling_style"),
            primary_role=attributes.get("primary_role"),
            is_wicketkeeper=attributes.get("is_wicketkeeper", False),
            international_team_id=attributes.get("international_team_id"),
            face_image_url=attributes.get("face_image_url"),
            is_active=True,
            source_provider_id=provider.pk,
            provider_player_id=provider_entity_id,
        )
        threshold = settings.DUPLICATE_CANDIDATE_THRESHOLD
        for candidate in candidates:
            if candidate.score >= threshold:
                PlayerDuplicateCandidate.objects.get_or_create(
                    candidate_player=player,
                    canonical_player_id=candidate.internal_id,
                    defaults={
                        "score": _as_score(candidate.score),
                        "feature_breakdown": candidate.breakdown,
                        "status": "open",
                    },
                )
        return new_id


__all__ = ["PlayerResolver"]

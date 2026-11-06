"""Provider-supplied player rankings (DATABASE_SCHEMA §3.7).

``player_ranking`` is **provider data**, never computed here: rating points and
any ICC/proprietary rating algorithm are out of scope (the brief, RSK-12). Rows
are imported by the ``rankings`` import pipeline (Group 5) through the
``get_rankings(gender)`` adapter method and idempotent by the schema's
``ux_ranking (player_id, gender, format_type, category, as_of_date)`` unique
(ledger decision 4: the schema wins over the brief's "(…, rank)" key — one row
per player per date). History is kept as successive dated snapshots; ``trend``
is derived from the previous snapshot unless the provider supplies it.
"""

from __future__ import annotations

from django.db import models

from apps.core.enums import FormatType, RankingCategory
from apps.core.models import UUIDPrimaryKeyModel


class PlayerRanking(UUIDPrimaryKeyModel):
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="rankings",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="rankings",
        db_index=False,
    )
    gender = models.CharField(max_length=1)
    format_type = models.TextField(choices=FormatType.choices)
    category = models.TextField(choices=RankingCategory.choices)
    rank = models.IntegerField()
    rating = models.IntegerField(null=True, blank=True)
    points = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    trend = models.TextField(null=True, blank=True)  # e.g. "+1", "0", "-2" (derived or provider)
    best_rank = models.IntegerField(null=True, blank=True)
    best_rating = models.IntegerField(null=True, blank=True)
    as_of_date = models.DateField()
    source_provider_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "player_ranking"
        constraints = [
            models.UniqueConstraint(
                fields=["player_id", "gender", "format_type", "category", "as_of_date"],
                name="ux_ranking",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gender", "format_type", "category", "rank", "-as_of_date"],
                name="ix_ranking_lookup",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} {self.format_type} {self.category} #{self.rank}"

    @classmethod
    def idempotency_key(
        cls, *, player_id: str, gender: str, format_type: str, category: str, as_of_date: str
    ) -> dict[str, object]:
        """The schema's idempotency key for an import row (ledger decision 4)."""
        return {
            "player_id": player_id,
            "gender": gender,
            "format_type": format_type,
            "category": category,
            "as_of_date": as_of_date,
        }

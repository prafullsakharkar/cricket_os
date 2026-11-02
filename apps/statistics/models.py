"""Per-match player statistics (Phase C scope: ``player_match_stat`` only).

``player_match_stat`` is built from the scoring engine's per-match lines when a
match completes (``match.completed``) or a correction is applied
(``correction.applied``) — never on per-delivery events (ARCHITECTURE §3,
SCORING_RULES §6). ``is_partial`` flags matches imported through the summary-only
path where ball-dependent metrics are estimated or absent; ``revision`` tracks
rebuilds so a correction supersedes the previous stat row (both columns are
Phase C additions to the §3.7 DDL — recorded in the roadmap's Phase C
decisions).
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.models import UUIDPrimaryKeyModel


class PlayerMatchStat(UUIDPrimaryKeyModel):
    match = models.ForeignKey(
        "matches.Match",
        on_delete=models.PROTECT,
        related_name="player_stats",
        db_index=False,
    )
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.PROTECT,
        related_name="match_stats",
        db_index=False,
    )
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="match_stats",
        db_index=False,
    )
    innings = models.ForeignKey(
        "innings.Innings",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="player_stats",
        db_index=False,
    )
    did_bat = models.BooleanField(default=False)
    did_bowl = models.BooleanField(default=False)
    did_field = models.BooleanField(default=False)
    is_playing_xi = models.BooleanField(default=True)
    batting = models.JSONField(default=dict)
    bowling = models.JSONField(default=dict)
    fielding = models.JSONField(default=dict)
    source_provider_id = models.UUIDField(null=True, blank=True)
    computed_at = models.DateTimeField(default=timezone.now)
    # Phase C additions (see module docstring).
    is_partial = models.BooleanField(default=False)
    revision = models.IntegerField(default=1)

    class Meta:
        db_table = "player_match_stat"
        constraints = [
            models.UniqueConstraint(fields=["match_id", "player_id"], name="ux_player_match_stat"),
        ]
        indexes = [
            models.Index(fields=["player_id"], name="ix_pms_player"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} in {self.match_id}"

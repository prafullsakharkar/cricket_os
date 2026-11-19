"""Fantasy read-side selectors (Task 3).

One selector gathers every canonical row the snapshot builder needs, so the
query count is bounded and the builder itself stays a pure assembly function
(no ORM). The fantasy module reads the same canonical statistics rows the
Phase F aggregates consume — never ``delivery``.
"""

from __future__ import annotations

from typing import Any

from apps.matches.models import Match, MatchAward, MatchPlayer
from apps.statistics.models import PlayerMatchStat


def match_fantasy_source(match_id: Any) -> dict[str, Any]:
    """Fetch one match's fantasy-relevant canonical rows.

    Returns::

        {
          "match": Match | None,
          "result": dict | None,   # result_type/is_super_over/is_dls
          "match_players": [...],  # player_id/is_playing_xi/is_substitute
          "awards": [...],         # player_id/award_type
          "stats": [...],          # player_id/did_*/batting/bowling/fielding/is_partial
        }

    Four indexed queries regardless of squad size.
    """
    match = Match.objects.filter(pk=match_id).select_related("result").first()
    result = None
    if match is not None and getattr(match, "result", None) is not None:
        result = {
            "result_type": match.result.result_type,
            "is_super_over": match.result.is_super_over,
            "is_dls": match.result.is_dls,
        }
    match_players = list(
        MatchPlayer.objects.filter(match_id=match_id).values(
            "player_id", "is_playing_xi", "is_substitute"
        )
    )
    awards = list(MatchAward.objects.filter(match_id=match_id).values("player_id", "award_type"))
    stats = list(
        PlayerMatchStat.objects.filter(match_id=match_id).values(
            "player_id",
            "did_bat",
            "did_bowl",
            "did_field",
            "is_playing_xi",
            "batting",
            "bowling",
            "fielding",
            "is_partial",
        )
    )
    return {
        "match": match,
        "result": result,
        "match_players": match_players,
        "awards": awards,
        "stats": stats,
    }

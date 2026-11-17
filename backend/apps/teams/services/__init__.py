"""Team-domain write services (six-layer rule: selectors never import these)."""

from __future__ import annotations

from apps.teams.services.teams import TeamResolver, upsert_team

__all__ = ["TeamResolver", "upsert_team"]

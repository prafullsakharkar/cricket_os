"""The ProviderAdapter protocol every provider integration implements.

This is the interface from DATA_INGESTION §2. Phase B defines it and the
registry; Phase C/D supply real adapters (starting with ``cricsheet`` and the
internal ``ReplayAdapter``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can supply; the UI degrades gracefully per capability."""

    ball_by_ball: bool = False
    commentary: bool = False
    historical_depth: bool = False
    push: bool = False
    fantasy_feed: bool = False
    features: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date


@runtime_checkable
class ProviderAdapter(Protocol):
    """Contract every adapter implements (DATA_INGESTION §2)."""

    def list_fixtures(self, window: DateRange) -> Iterable[Any]: ...
    def get_match(self, provider_match_id: str) -> Any: ...
    def get_scorecard(self, provider_match_id: str) -> Any: ...
    def iter_deliveries(self, provider_match_id: str, since_seq: int | None) -> Iterable[Any]: ...
    def get_commentary(self, provider_match_id: str) -> Iterable[Any]: ...
    def get_rankings(self, gender: str) -> Iterable[Any]: ...
    def normalize(self, raw: Any) -> Any: ...
    def capabilities(self) -> ProviderCapabilities: ...


__all__ = ["DateRange", "ProviderAdapter", "ProviderCapabilities"]

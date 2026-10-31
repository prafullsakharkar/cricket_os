"""ProviderAdapter contract + capability dataclasses (apps/providers/base.py).

The adapter interface is Phase C/D scaffolding; these tests lock in its shape
so a real adapter (Cricsheet, ReplayAdapter, …) is recognised by the
runtime-checkable protocol and the capability flags degrade UI correctly.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from typing import Any

from apps.providers.base import (
    DateRange,
    ProviderAdapter,
    ProviderCapabilities,
)


class _FakeAdapter:
    """A minimal adapter implementing the ProviderAdapter protocol shape."""

    def list_fixtures(self, window: DateRange) -> Iterable[Any]:
        return [window]

    def get_match(self, provider_match_id: str) -> Any:
        return {"id": provider_match_id}

    def get_scorecard(self, provider_match_id: str) -> Any:
        return {}

    def iter_deliveries(self, provider_match_id: str, since_seq: int | None) -> Iterable[Any]:
        return []

    def get_commentary(self, provider_match_id: str) -> Iterable[Any]:
        return []

    def get_rankings(self, gender: str) -> Iterable[Any]:
        return []

    def normalize(self, raw: Any) -> Any:
        return raw

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(ball_by_ball=True)


class TestProviderCapabilities:
    def test_defaults_are_all_false(self) -> None:
        caps = ProviderCapabilities()
        assert caps.ball_by_ball is False
        assert caps.commentary is False
        assert caps.historical_depth is False
        assert caps.push is False
        assert caps.fantasy_feed is False
        assert caps.features == ()

    def test_override_and_feature_list(self) -> None:
        caps = ProviderCapabilities(
            ball_by_ball=True, commentary=True, features=("cricsheet", "replay")
        )
        assert caps.ball_by_ball is True
        assert caps.commentary is True
        assert caps.features == ("cricsheet", "replay")
        # Frozen dataclass: no mutation.
        assert caps == ProviderCapabilities(
            ball_by_ball=True, commentary=True, features=("cricsheet", "replay")
        )


class TestDateRange:
    def test_holds_start_and_end(self) -> None:
        start = datetime.date(2026, 1, 1)
        end = datetime.date(2026, 1, 31)
        window = DateRange(start=start, end=end)
        assert window.start == start
        assert window.end == end


class TestProviderAdapterProtocol:
    def test_recognises_conforming_adapter(self) -> None:
        adapter = _FakeAdapter()
        assert isinstance(adapter, ProviderAdapter)
        assert isinstance(adapter.capabilities(), ProviderCapabilities)
        assert adapter.capabilities().ball_by_ball is True

    def test_rejects_non_conforming_object(self) -> None:
        assert not isinstance(object(), ProviderAdapter)
        assert not isinstance(ProviderCapabilities(), ProviderAdapter)


__all__: list[str] = []

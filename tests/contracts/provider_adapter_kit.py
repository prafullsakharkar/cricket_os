"""Reusable ``ProviderAdapter`` contract suite (DATA_INGESTION §2).

Every provider adapter — the internal ``ReplayAdapter`` and any future
licensed adapter (post Phase-0 gate) — must pass this kit, because it pins
exactly the shape the live ingestion surface consumes:

* ``capabilities()`` returns a ``ProviderCapabilities``;
* ``list_fixtures(window)`` yields ``FixtureRecord`` objects covering the
  window (used by fixture sync);
* ``get_match(id)`` / ``get_state(id)`` return a normalized shell with a
  ``LiveState``;
* ``iter_deliveries(id, since_seq)`` yields ``LiveInningsBatch`` records with
  monotonic ``delivery_seq`` and correct ``since_seq`` resume semantics
  (polling resumes from the committed checkpoint);
* ``get_scorecard(id)`` returns the provider card shape;
* ``normalize(raw)`` maps a raw payload to the normalized record.

Usage: subclass and implement ``make_adapter()`` and ``make_recorded_match()``
(a ``Match`` with stored deliveries, e.g. via the Phase C factories + the live
write path). The class is deliberately *not* collected by pytest itself
(name does not match ``python_classes``); only ``Test*`` subclasses run.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.live.services.apply import LiveInningsBatch, LiveState
from apps.providers.base import DateRange, ProviderCapabilities
from apps.synchronization.services.fixtures import FixtureRecord


class ProviderAdapterKit:
    """Contract suite — subclass and implement the two ``make_*`` methods."""

    def make_adapter(self) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def make_recorded_match(self) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    @pytest.fixture
    def adapter(self) -> Any:
        return self.make_adapter()

    @pytest.fixture
    def recorded_match(self) -> Any:
        return self.make_recorded_match()

    def test_capabilities_are_provider_capabilities(self, adapter: Any) -> None:
        caps = adapter.capabilities()
        assert isinstance(caps, ProviderCapabilities)
        assert isinstance(caps.ball_by_ball, bool)
        assert isinstance(caps.push, bool)

    def test_list_fixtures_covers_recorded_match(self, adapter: Any, recorded_match: Any) -> None:
        window = DateRange(
            start=recorded_match.source_local_date, end=recorded_match.source_local_date
        )
        fixtures = list(adapter.list_fixtures(window))
        ids = {f.provider_match_id for f in fixtures}
        assert str(recorded_match.id) in ids
        first = next(f for f in fixtures if f.provider_match_id == str(recorded_match.id))
        assert isinstance(first, FixtureRecord)

    def test_get_match_returns_state_shell(self, adapter: Any, recorded_match: Any) -> None:
        shell = adapter.get_match(str(recorded_match.id))
        assert shell["match_id"] == str(recorded_match.id)
        assert isinstance(shell["state"], LiveState)

    def test_get_state_returns_live_state(self, adapter: Any, recorded_match: Any) -> None:
        state = adapter.get_state(str(recorded_match.id))
        assert isinstance(state, LiveState)
        assert state.status == recorded_match.status

    def test_iter_deliveries_returns_live_batches(self, adapter: Any, recorded_match: Any) -> None:
        batches = list(adapter.iter_deliveries(str(recorded_match.id), None))
        assert batches, "a recorded match must yield at least one batch"
        innings_seqs: dict[int, list[int]] = {}
        for batch in batches:
            assert isinstance(batch, LiveInningsBatch)
            seqs = [d.delivery_seq for d in batch.drafts]
            assert all(s > 0 for s in seqs)
            # delivery_seq is monotonic per innings (never globally unique).
            assert seqs == sorted(seqs), "deliveries must be in delivery_seq order"
            innings_seqs.setdefault(batch.innings_number, []).extend(seqs)
        for seqs in innings_seqs.values():
            assert len(set(seqs)) == len(seqs), "an innings must not repeat a delivery_seq"

    def test_iter_deliveries_resumes_from_since_seq(
        self, adapter: Any, recorded_match: Any
    ) -> None:
        batches = list(adapter.iter_deliveries(str(recorded_match.id), None))
        seqs = [d.delivery_seq for batch in batches for d in batch.drafts]
        last = max(seqs)
        resumed = list(adapter.iter_deliveries(str(recorded_match.id), last))
        resumed_seqs = [d.delivery_seq for batch in resumed for d in batch.drafts]
        assert all(s > last for s in resumed_seqs)

    def test_get_scorecard_returns_provider_card(self, adapter: Any, recorded_match: Any) -> None:
        card = adapter.get_scorecard(str(recorded_match.id))
        assert card["match_id"] == str(recorded_match.id)
        assert isinstance(card["summaries"], list)

    def test_normalize_passes_through(self, adapter: Any) -> None:
        assert adapter.normalize("raw") == "raw"

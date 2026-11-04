"""Composition root: wiring defaults + config.asgi builds the real components.

The realtime package never imports ``apps.matches``/``apps.providers`` (the
import-linter contract); the composition root (``config/asgi.py``) injects the
implementations. These tests lock that boundary and the fallback behaviour.
"""

from __future__ import annotations

import pytest

from apps.core.streaming import StreamInfo
from apps.realtime import wiring
from apps.realtime.hub import Hub

pytestmark = pytest.mark.django_db


class _DummyReader:
    def events_since(self, match_id: str, last_seq: int) -> StreamInfo:
        return StreamInfo()

    def latest_seq(self, match_id: str) -> int:
        return 0


@pytest.fixture(autouse=True)
def _reset_wiring() -> object:
    wiring.reset()
    yield
    wiring.reset()


def test_unconfigured_reader_raises() -> None:
    assert wiring.configured() is False
    with pytest.raises(RuntimeError):
        wiring.get_reader()


def test_configure_round_trip() -> None:
    reader = _DummyReader()
    wiring.configure(reader=reader)
    assert wiring.configured() is True
    assert wiring.get_reader() is reader
    assert wiring.get_policy() is None


def test_reset_clears_configured_implementations() -> None:
    wiring.configure(reader=_DummyReader())
    wiring.reset()
    assert wiring.configured() is False


def test_hub_falls_back_to_wired_reader() -> None:
    reader = _DummyReader()
    wiring.configure(reader=reader)
    hub = Hub()  # no explicit reader
    assert hub.reader is reader


def test_asgi_composition_root_wires_matches_implementations() -> None:
    import config.asgi

    hub, broker, wired = config.asgi.build_realtime_components()
    assert wired.get_reader() is not None
    assert wired.get_policy() is not None
    assert hub.reader is wired.get_reader()
    assert hub.policy is wired.get_policy()
    assert hub.active_match_ids() == []
    assert broker.degraded is False

"""Composition root: wiring defaults + config.asgi builds the real components.

The realtime package never imports ``apps.matches``/``apps.providers`` (the
import-linter contract); the composition root (``config/asgi.py``) injects the
implementations. These tests lock that boundary and the fallback behaviour.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

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


class _FakeBroker:
    """Broker stand-in: ``run`` blocks until ``stop`` releases it."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self._stop = asyncio.Event()

    async def run(self) -> None:
        self.started = True
        await self._stop.wait()

    async def stop(self) -> None:
        self.stopped = True
        self._stop.set()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_lifespan_startup_builds_and_runs_the_broker() -> None:
    import config.asgi

    async def scenario() -> None:
        hub = object()
        broker = _FakeBroker()
        calls: list[int] = []

        def factory() -> tuple[Any, Any, Any]:
            calls.append(1)
            return hub, broker, None

        app = config.asgi._RealtimeLifespan(factory=factory)
        inputs: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        outputs: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        task = asyncio.create_task(app({"type": "lifespan"}, inputs.get, outputs.put))
        await inputs.put({"type": "lifespan.startup"})
        assert (await asyncio.wait_for(outputs.get(), 1)) == {"type": "lifespan.startup.complete"}
        await asyncio.sleep(0.05)  # let the broker task start
        assert calls == [1]
        assert broker.started is True
        await inputs.put({"type": "lifespan.shutdown"})
        assert (await asyncio.wait_for(outputs.get(), 1)) == {"type": "lifespan.shutdown.complete"}
        assert broker.stopped is True
        await task

    _run(scenario())


def test_lifespan_startup_failure_reports_and_terminates() -> None:
    import config.asgi

    async def scenario() -> None:
        def bad_factory() -> Any:
            raise RuntimeError("boom")

        app = config.asgi._RealtimeLifespan(factory=bad_factory)
        inputs: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        outputs: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        task = asyncio.create_task(app({"type": "lifespan"}, inputs.get, outputs.put))
        await inputs.put({"type": "lifespan.startup"})
        assert (await asyncio.wait_for(outputs.get(), 1)) == {"type": "lifespan.startup.failed"}
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)

    _run(scenario())

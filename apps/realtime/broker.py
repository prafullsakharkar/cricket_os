"""Redis pub/sub -> hub bridge with degraded polling and recovery.

Topology (PERFORMANCE §8): the scoring engine publishes each committed event to
``match:{id}``; every web node runs one :class:`Broker` that pattern-subscribes
to ``match:*`` and feeds the local :class:`apps.realtime.hub.Hub`.

* **Best effort** — a Redis outage must degrade realtime delivery, never take
  the node down. After ``max_errors`` consecutive transport failures the broker
  enters **degraded mode**: it polls the durable event log (via the injected
  reader) for every match with subscribers, and periodically probes the
  transport for recovery.
* **Invalid frames** are counted and dropped; a single corrupt message never
  kills the loop.

The transport is injectable so tests use fakes (no network); the default wraps
the ``pubsub`` Redis role.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Protocol

from apps.core import metrics
from apps.realtime.frames import FrameError, from_stream_event, parse_frame
from apps.realtime.hub import Hub

logger = logging.getLogger("apps.realtime.broker")

#: Default channel pattern: every match fan-out channel.
CHANNEL_PATTERN = "match:*"


class PubSubTransport(Protocol):
    """Anything that can deliver raw pub/sub messages for a channel pattern."""

    def subscribe(self, pattern: str) -> None: ...
    def messages(self, timeout: float | None = 0.0) -> list[str]: ...
    def ping(self) -> bool: ...
    def close(self) -> None: ...


class RedisPubSubTransport:
    """Real transport over the ``pubsub`` Redis role (injectable client)."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._pubsub: Any = None

    def _conn(self) -> Any:
        if self._client is not None:
            return self._client
        from apps.core.redis import ROLE_PUBSUB, client

        return client(ROLE_PUBSUB)

    def subscribe(self, pattern: str) -> None:
        self._pubsub = self._conn().pubsub()
        self._pubsub.psubscribe(pattern)

    def messages(self, timeout: float | None = 0.0) -> list[str]:
        if self._pubsub is None:
            return []
        raw = self._pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
        data = raw.get("data") if isinstance(raw, dict) else None
        return [data] if data else []

    def ping(self) -> bool:
        try:
            return bool(self._conn().ping())
        except Exception:
            return False

    def close(self) -> None:
        if self._pubsub is not None:
            with contextlib.suppress(Exception):  # pragma: no cover - teardown
                self._pubsub.close()
            self._pubsub = None


class Broker:
    """Owns the transport lifecycle and feeds the hub."""

    def __init__(
        self,
        hub: Hub,
        *,
        reader: Any | None = None,
        transport: PubSubTransport | None = None,
        channel_pattern: str = CHANNEL_PATTERN,
        max_errors: int = 3,
        poll_interval: float = 1.0,
        poll_timeout: float = 0.05,
    ) -> None:
        self._hub = hub
        self._reader = reader
        self._transport = transport or RedisPubSubTransport()
        self.channel_pattern = channel_pattern
        self.max_errors = max_errors
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._errors = 0
        self._degraded = False
        self._stop = False

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def reader(self) -> Any:
        if self._reader is not None:
            return self._reader
        return self._hub.reader

    # -- ingestion ----------------------------------------------------------- #

    async def process(self, raw: str) -> None:
        """Parse one transport message and route it to the hub."""
        try:
            frame = parse_frame(raw)
        except FrameError as exc:
            metrics.REALTIME_INVALID_FRAMES.inc()
            logger.warning("dropped invalid realtime frame", extra={"error": str(exc)})
            return
        await self._hub.publish(frame)

    # -- lifecycle ------------------------------------------------------------ #

    async def run(self) -> None:
        """Subscribe and loop until :meth:`stop` (drives the hub's ticker)."""
        self._hub.start()
        try:
            try:
                self._transport.subscribe(self.channel_pattern)
            except Exception:
                self._errors += 1
                self._enter_degraded()
            while not self._stop:
                if self._degraded:
                    await self._degraded_step()
                    await asyncio.sleep(self.poll_interval)
                else:
                    await self._pubsub_step()
                    await asyncio.sleep(0)
        finally:
            await self._hub.stop()

    async def stop(self) -> None:
        self._stop = True
        with contextlib.suppress(Exception):  # pragma: no cover - teardown
            await asyncio.to_thread(self._transport.close)

    async def _pubsub_step(self) -> None:
        try:
            raws = await asyncio.to_thread(self._transport.messages, self.poll_timeout)
        except Exception:
            self._errors += 1
            if self._errors >= self.max_errors:
                self._enter_degraded()
            return
        self._errors = 0
        for raw in raws:
            await self.process(raw)

    # -- degraded mode -------------------------------------------------------- #

    def _enter_degraded(self) -> None:
        if not self._degraded:
            self._degraded = True
            metrics.REALTIME_DEGRADED.set(1)
            logger.warning("realtime broker degraded: polling the event log")

    async def _degraded_step(self) -> None:
        """Probe the transport for recovery; otherwise poll the log."""
        try:
            recovered = await asyncio.to_thread(self._transport.ping)
        except Exception:
            recovered = False
        if recovered:
            self._degraded = False
            self._errors = 0
            metrics.REALTIME_DEGRADED.set(0)
            logger.info("realtime broker recovered")
            return
        await self.poll_once()

    async def poll_once(self) -> None:
        """Replay new log events for every match with subscribers."""
        for match_id in self._hub.active_match_ids():
            highest = self._hub.highest_seq(match_id)
            # The reader is sync ORM; Django forbids that from async code, so
            # run the poll off the event loop (degraded mode only).
            info = await asyncio.to_thread(self.reader.events_since, match_id, highest)
            for event in info.events:
                await self._hub.publish(from_stream_event(event))


__all__ = [
    "CHANNEL_PATTERN",
    "Broker",
    "PubSubTransport",
    "RedisPubSubTransport",
]

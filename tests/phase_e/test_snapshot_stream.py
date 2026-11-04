"""REST snapshot -> realtime subscribe, exactly once (API_SPEC §3.8, ledger #2).

Every match-scoped REST payload carries ``meta.event_seq`` (Group 3): the
highest committed ``match_event_log`` sequence reflected by the snapshot. A
client that subscribes with ``last_seq`` set to that value receives exactly
the events appended *after* the snapshot — no gap, no duplicate. That is the
REST-first recovery contract the SSE/WS transports build on.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from django.test import Client

from apps.matches import event_schemas
from apps.matches.services import event_log
from apps.matches.services.stream_reader import PostgresEventLogReader
from apps.realtime.frames import Frame
from apps.realtime.hub import Hub
from tests.phase_c.factories import make_match

pytestmark = pytest.mark.django_db(transaction=True)


def _append(match_id: Any, seq: int) -> None:
    event_log.append_event(match_id, event_schemas.DELIVERY_APPENDED, {"delivery_seq": seq})


async def _drain(queue: asyncio.Queue[Frame]) -> list[Frame]:
    frames: list[Frame] = []
    while not queue.empty():
        frames.append(await queue.get())
    return frames


def test_snapshot_then_subscribe_delivers_exactly_once() -> None:
    match = make_match()
    for seq in (1, 2, 3):
        _append(match.id, seq)

    response = Client().get(f"/api/v1/matches/{match.id}/")
    assert response.status_code == 200
    snapshot_seq = response.json()["meta"]["event_seq"]
    assert snapshot_seq == 3

    async def scenario() -> None:
        hub = Hub(reader=PostgresEventLogReader())

        # Subscribe at the snapshot position: nothing to replay yet.
        sub = await hub.subscribe(str(match.id), last_seq=snapshot_seq)
        assert sub.queue.empty()

        # A new event lands; the subscriber sees exactly that one event. The
        # append is a sync ORM write, so it runs off the loop like the hub's
        # reader calls (autocommit makes it visible to the reader thread).
        await asyncio.to_thread(_append, match.id, 4)
        await hub.publish(
            Frame(
                seq=4,
                event_type=event_schemas.DELIVERY_APPENDED,
                match_id=str(match.id),
                payload={"v": event_schemas.SCHEMA_VERSION, "delivery_seq": 4},
            )
        )
        await hub.tick()
        frames = await _drain(sub.queue)
        assert [f.seq for f in frames] == [4]
        assert sub.last_seq == 4

        # A fresh client from 0 replays the whole gapless run 1..4.
        fresh = await hub.subscribe(str(match.id), last_seq=0)
        assert [f.seq for f in await _drain(fresh.queue)] == [1, 2, 3, 4]
        hub.unsubscribe(sub)
        hub.unsubscribe(fresh)

    asyncio.run(scenario())


def test_snapshot_without_events_subscribes_live() -> None:
    match = make_match()
    response = Client().get(f"/api/v1/matches/{match.id}/")
    assert response.status_code == 200
    assert response.json()["meta"]["event_seq"] == 0

    async def scenario() -> None:
        hub = Hub(reader=PostgresEventLogReader())
        sub = await hub.subscribe(str(match.id), last_seq=0)
        assert sub.queue.empty()  # empty log: nothing to replay, live only
        await hub.publish(
            Frame(
                seq=1,
                event_type=event_schemas.MATCH_STATE_CHANGED,
                match_id=str(match.id),
                payload={"v": event_schemas.SCHEMA_VERSION},
            )
        )
        await hub.tick()
        frames = await _drain(sub.queue)
        assert [f.seq for f in frames] == [1]
        hub.unsubscribe(sub)

    asyncio.run(scenario())

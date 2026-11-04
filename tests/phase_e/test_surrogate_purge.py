"""Surrogate-key purging on completion and correction (PERFORMANCE §5, TESTING §10).

A CDN sees opaque ``Surrogate-Key`` tags, not our version namespaces. The
invalidation registry bumps versions for every mapped topic; *additionally*,
completion and correction events purge the CDN by tag because they change
history and must not be served stale at the edge. The purger is injectable: a
recording fake for tests, a no-op by default, an HTTP purger when a CDN URL is
configured.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from apps.core import events
from apps.core.cache import invalidation, purger

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_purger() -> Any:
    invalidation.configure_purger(purger.NoopCachePurger())
    yield
    invalidation.configure_purger(purger.NoopCachePurger())


def test_recording_purger_records_deduplicated_tags() -> None:
    fake = purger.RecordingCachePurger()
    fake.purge(["match-a", "match-a", "series-b"])
    assert fake.calls == [["match-a", "series-b"]]


def test_completion_purges_match_and_series_tags() -> None:
    fake = purger.RecordingCachePurger()
    invalidation.configure_purger(fake)
    match_id = str(uuid4())
    series_id = str(uuid4())

    invalidation.invalidate_event(
        events.TOPIC_MATCH_COMPLETED,
        {"match_id": match_id, "series_id": series_id},
    )
    assert fake.calls == [[f"match-{match_id}", f"series-{series_id}"]]


def test_correction_purges_the_match_tag() -> None:
    fake = purger.RecordingCachePurger()
    invalidation.configure_purger(fake)
    match_id = str(uuid4())

    invalidation.invalidate_event(events.TOPIC_CORRECTION_APPLIED, {"match_id": match_id})
    assert fake.calls == [[f"match-{match_id}"]]


def test_ordinary_deltas_do_not_purge() -> None:
    fake = purger.RecordingCachePurger()
    invalidation.configure_purger(fake)

    invalidation.invalidate_event(
        events.TOPIC_DELIVERY_APPENDED,
        {"match_id": str(uuid4()), "innings_id": str(uuid4())},
    )
    assert fake.calls == []


def test_missing_payload_fields_do_not_break_purge() -> None:
    fake = purger.RecordingCachePurger()
    invalidation.configure_purger(fake)

    invalidation.invalidate_event(events.TOPIC_MATCH_COMPLETED, {})
    assert fake.calls == []


def test_purge_runs_through_the_event_bus_subscriber() -> None:
    fake = purger.RecordingCachePurger()
    invalidation.configure_purger(fake)
    match_id = str(uuid4())

    events.publish(
        events.TOPIC_MATCH_COMPLETED,
        {"match_id": match_id},
        on_commit=False,
    )
    assert fake.calls == [[f"match-{match_id}"]]


def test_default_purger_is_a_noop() -> None:
    # No CDN configured: the version-bump registry handles origin invalidation;
    # the CDN purge hook must be inert, not error.
    invalidation.configure_purger(purger.NoopCachePurger())
    invalidation.invalidate_event(
        events.TOPIC_MATCH_COMPLETED,
        {"match_id": str(uuid4())},
    )
    assert True

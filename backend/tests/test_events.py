"""Unit + transaction tests for the in-process event bus (core/events.py).

The bus matches ARCHITECTURE §5: events publish *after commit*
(``transaction.on_commit``) so consumers never observe rolled-back data, with
``on_commit=False`` reserved for same-transaction subscribers (audit).
"""

from __future__ import annotations

import pytest
from django.db import transaction

from apps.core import events

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_events() -> object:
    events.clear_subscriptions()
    yield
    events.clear_subscriptions()


def test_subscribe_and_publish_delivers_payload() -> None:
    received: list[object] = []
    events.subscribe("t", received.append)
    events.publish("t", {"a": 1}, on_commit=False)
    assert received == [{"a": 1}]


def test_multiple_handlers_all_called() -> None:
    received: list[str] = []
    events.subscribe("t", lambda p: received.append(f"h1:{p}"))
    events.subscribe("t", lambda p: received.append(f"h2:{p}"))
    events.publish("t", "x", on_commit=False)
    assert received == ["h1:x", "h2:x"]


def test_handlers_only_receive_their_topic() -> None:
    received: list[str] = []
    events.subscribe("a", lambda _p: received.append("a"))
    events.subscribe("b", lambda _p: received.append("b"))
    events.publish("a", None, on_commit=False)
    events.publish("b", None, on_commit=False)
    assert received == ["a", "b"]


def test_clear_subscriptions_resets_registry() -> None:
    received: list[str] = []
    events.subscribe("t", lambda _p: received.append("x"))
    events.clear_subscriptions()
    events.publish("t", None, on_commit=False)
    assert received == []


def test_topic_constants_are_documented_strings() -> None:
    assert events.TOPIC_PLAYER_MERGED == "player.merged"
    assert events.TOPIC_PROVIDER_MAP_VERIFIED == "provider_map.verified"
    assert events.TOPIC_DUPLICATE_REVIEWED == "player_duplicate.reviewed"


@pytest.mark.django_db(transaction=True)
def test_on_commit_default_fires_after_commit() -> None:
    """Default publish() must not run handlers until the transaction commits."""
    received: list[str] = []
    events.subscribe("t", lambda p: received.append(p))
    with transaction.atomic():
        events.publish("t", "x")
        assert received == [], "handler ran before the transaction committed"
    assert received == ["x"]


@pytest.mark.django_db(transaction=True)
def test_on_commit_does_not_fire_when_transaction_rolls_back() -> None:
    received: list[str] = []
    events.subscribe("t", lambda p: received.append(p))
    with pytest.raises(RuntimeError), transaction.atomic():
        events.publish("t", "x")
        raise RuntimeError("boom")
    assert received == [], "post-commit handler must not fire on rollback"


@pytest.mark.django_db(transaction=True)
def test_on_commit_false_fires_synchronously_inside_transaction() -> None:
    received: list[str] = []
    events.subscribe("t", lambda p: received.append(p))
    with transaction.atomic():
        events.publish("t", "x", on_commit=False)
        assert received == ["x"], "synchronous subscriber must see the event in-transaction"

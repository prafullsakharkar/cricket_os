"""Celery: eager execution, task registration, request-id propagation."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def test_ping_task_runs_eagerly() -> None:
    from apps.core.tasks import ping

    result = ping.apply()
    assert result.get() == "pong"


def test_ping_task_is_named() -> None:
    from apps.core.tasks import ping

    assert ping.name == "core.ping"


def test_inject_request_id_copies_contextvar_onto_headers() -> None:
    from apps.core.logging import reset_request_id, set_request_id
    from config.celery import inject_request_id

    set_request_id("req-abc")
    try:
        headers: dict[str, Any] = {}
        inject_request_id(headers=headers)
        assert headers["request_id"] == "req-abc"
    finally:
        reset_request_id()


def test_inject_request_id_noops_without_a_request_id() -> None:
    from apps.core.logging import reset_request_id
    from config.celery import inject_request_id

    reset_request_id()
    headers: dict[str, Any] = {}
    inject_request_id(headers=headers)
    assert headers == {}


def test_bind_request_id_sets_the_contextvar_from_the_task() -> None:
    from apps.core.logging import get_request_id, reset_request_id
    from config.celery import bind_request_id

    class FakeTask:
        class request:
            request_id = "req-xyz"

    bind_request_id(task=FakeTask())
    try:
        assert get_request_id() == "req-xyz"
    finally:
        reset_request_id()


def test_celery_app_uses_json_serialization() -> None:
    from config.celery import app

    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert app.conf.task_acks_late is True

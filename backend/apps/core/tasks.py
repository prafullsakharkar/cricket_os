"""Celery tasks for the core app. Phase A only needs a smoke task."""

from __future__ import annotations

from config.celery import app


@app.task(name="core.ping")  # type: ignore[untyped-decorator]
def ping() -> str:
    """Return ``"pong"`` — proves the worker/broker path is wired up."""
    return "pong"

"""Celery application for Cricket OS.

Reads configuration from Django settings under the ``CELERY`` namespace,
autodiscovers ``tasks.py`` in every installed app, and wires request-id
propagation so a task's logs carry the originating HTTP request id.
"""

from __future__ import annotations

import os
from typing import Any

from celery.signals import before_task_publish, task_postrun, task_prerun

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("cricket_os")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

REQUEST_ID_HEADER = "request_id"


@before_task_publish.connect  # type: ignore[untyped-decorator]
def inject_request_id(headers: dict[str, Any] | None = None, **kwargs: Any) -> None:
    """Copy the current request id onto the outgoing task headers."""
    # Imported lazily so the Celery module can be imported without Django ready.
    from apps.core.logging import get_request_id

    request_id = get_request_id()
    if request_id and headers is not None:
        headers[REQUEST_ID_HEADER] = request_id


@task_prerun.connect  # type: ignore[untyped-decorator]
def bind_request_id(task: Any = None, **kwargs: Any) -> None:
    """Expose the propagated request id to the worker's logging context."""
    from apps.core.logging import set_request_id

    request_id = getattr(task.request, REQUEST_ID_HEADER, None)
    if request_id:
        set_request_id(request_id)


@task_postrun.connect  # type: ignore[untyped-decorator]
def clear_request_id(**kwargs: Any) -> None:
    """Reset the request-id contextvar once the task finishes."""
    from apps.core.logging import reset_request_id

    reset_request_id()

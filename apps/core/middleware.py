"""Request-id middleware.

Reads ``X-Request-ID`` or generates one, binds it to a contextvar (so every
log line and outbound Celery task carries it), echoes it on the response, and
logs one structured line per request.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.core.logging import reset_request_id, set_request_id
from apps.core.net import client_ip

logger = logging.getLogger("apps.core.middleware")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware:
    """Correlate and time every request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.META.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4())
        token = set_request_id(request_id)
        start = time.perf_counter()
        try:
            response = self.get_response(request)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            reset_request_id(token)

        response[REQUEST_ID_HEADER] = request_id
        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                # Trusted-proxy aware (SECURITY §2); used by rate limiting too.
                "client_ip": client_ip(request),
            },
        )
        return response

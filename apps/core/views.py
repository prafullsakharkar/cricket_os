"""Core views. Phase A exposes only the unauthenticated health endpoint;
Phase D adds the settings-gated internal ``/metrics`` exposition."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.http import HttpResponse
from django.views import View
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import health
from apps.core.errors import CODE_MESSAGES, error_payload

logger = logging.getLogger("apps.core.views")


@extend_schema(exclude=True)
class HealthView(APIView):
    """Liveness/readiness probe: reports database and Redis reachability.

    Returns 200 with ``{"data": {"status": "ok", "checks": {...}}}`` when both
    dependencies answer, otherwise 503 with the ``upstream_unavailable``
    envelope naming the failing check(s). Never requires authentication.

    Excluded from the OpenAPI schema: it is an ops probe, not a data endpoint.
    """

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = ()

    def get(self, request: Request) -> Response:
        checks: dict[str, str] = {}
        healthy = True
        probes: tuple[tuple[str, Callable[[], None]], ...] = (
            ("database", health.check_database),
            ("redis", health.check_redis),
        )
        for name, probe in probes:
            try:
                probe()
            except Exception:
                logger.warning("health check failed: %s", name, exc_info=True)
                checks[name] = "error"
                healthy = False
            else:
                checks[name] = "ok"

        if healthy:
            response = Response({"data": {"status": "ok", "checks": checks}})
        else:
            response = Response(
                error_payload(
                    "upstream_unavailable",
                    CODE_MESSAGES["upstream_unavailable"],
                    {"checks": checks},
                ),
                status=503,
            )
        response["Cache-Control"] = "no-cache"
        return response


class MetricsView(View):
    """Internal Prometheus exposition endpoint (settings-gated, never public).

    Returns the metrics facade's text exposition (prometheus_client when
    installed, else the in-process fallback renderer) with ``text/plain;
    version=0.0.4`` and ``Cache-Control: no-cache``. Registered in the root
    URLconf only when ``ENABLE_METRICS_ENDPOINT`` is on, so a disabled
    deployment 404s the path entirely (SECURITY §12: ops surfaces are not
    exposed publicly).
    """

    def get(self, request: Any) -> HttpResponse:
        from apps.core import metrics

        response = HttpResponse(
            metrics.render(),
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )
        response["Cache-Control"] = "no-cache"
        return response

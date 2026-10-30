"""Core views. Phase A exposes only the unauthenticated health endpoint."""

from __future__ import annotations

import logging
from collections.abc import Callable

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

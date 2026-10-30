"""Development settings: DEBUG on by default, permissive hosts."""

from __future__ import annotations

from .base import *
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

# In development it is convenient to accept any host (uvicorn --reload, LAN).
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["*"])

# The browsable API is off (JSON only); add DRF debug niceties here if needed.

# Serve the OpenAPI schema at /api/v1/schema in development.
ENABLE_API_SCHEMA = True

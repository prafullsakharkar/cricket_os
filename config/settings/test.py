"""Test settings: fast hashing, eager Celery, in-memory cache, permissive hosts.

The database still comes from ``DATABASE_URL`` (a disposable PostgreSQL
database); pytest-django creates and reuses ``test_<name>`` from it.
"""

from __future__ import annotations

from .base import *
from .base import REST_FRAMEWORK, env

DEBUG = False
ALLOWED_HOSTS = ["*"]

# Fast (and irrelevant for API tests) password hashing.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# In-memory cache so tests never depend on Redis being up unless they ask for it.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cricket-test-cache",
    }
}

# Tasks run inline and raise in the caller.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Keep the throttle classes wired but with limits high enough not to trip
# unrelated tests; throttle tests override rates explicitly.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"anon": "10000/min", "user": "10000/min"},
}

THROTTLE_ANON = env("THROTTLE_ANON", default="10000/min")

# Serve the OpenAPI schema in tests (schemathesis contract tests read it).
ENABLE_API_SCHEMA = True

# Serve the internal /metrics exposition in tests so its contract is verified.
ENABLE_METRICS_ENDPOINT = True

# Test-only app proving the player-reference registry is extensible
# (Group 4). Never installed outside the test settings.
INSTALLED_APPS = [*INSTALLED_APPS, "tests.dummy_app"]

"""Base Django settings shared by every environment.

All runtime configuration comes from the environment via ``django-environ``;
``.env.example`` documents every variable. ``dev`` / ``prod`` / ``test`` import
``*`` from here and override the few things that differ.
"""

from __future__ import annotations

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

# --------------------------------------------------------------------------- #
# Paths & environment
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_CSRF_TRUSTED_ORIGINS=(list, []),
    DJANGO_LOG_LEVEL=(str, "INFO"),
    DB_CONN_MAX_AGE=(int, 60),
    DB_SSLMODE=(str, "prefer"),
    REDIS_URL=(str, "redis://localhost:6379/0"),
    CELERY_BROKER_URL=(str, "redis://localhost:6379/1"),
    CELERY_RESULT_BACKEND=(str, "redis://localhost:6379/2"),
    CELERY_TASK_ALWAYS_EAGER=(bool, False),
    THROTTLE_ANON=(str, "120/min"),
    THROTTLE_USER=(str, "600/min"),
)

# Read a local .env when present. Never committed (see .gitignore).
environ.Env.read_env(BASE_DIR / ".env")

DEV_INSECURE_SECRET_KEY = "insecure-dev-key-change-me"

SECRET_KEY = env("DJANGO_SECRET_KEY", default=DEV_INSECURE_SECRET_KEY)
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS")

# The dedicated PostgreSQL schema every table lives in.
DB_SCHEMA = "cricket"

# --------------------------------------------------------------------------- #
# Applications
# --------------------------------------------------------------------------- #
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "channels",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.core",
    "apps.venues",
    "apps.teams",
    "apps.players",
    "apps.competitions",
    "apps.providers",
    "apps.administration",
    "apps.matches",
    "apps.innings",
    "apps.deliveries",
    "apps.scorecards",
    "apps.statistics",
    "apps.ingestion",
    "apps.synchronization",
    "apps.live",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# drf-spectacular: OpenAPI schema generation. The schema is served at
# /api/v1/schema in dev/test only (ENABLE_API_SCHEMA); it feeds the
# contract tests (schemathesis) mandated by TESTING_STRATEGY §4.
SPECTACULAR_SETTINGS = {
    "TITLE": "Cricket OS API",
    "DESCRIPTION": "Read-only public surface for Cricket OS reference data.",
    "VERSION": "v1",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

#: Serve /api/v1/schema only in dev/test (set True there, False in prod).
ENABLE_API_SCHEMA = env("DJANGO_ENABLE_API_SCHEMA", default=False)

MIDDLEWARE = [
    # First, so it wraps everything and can time/log the whole request.
    "apps.core.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------- #
# Database — PostgreSQL 17 via psycopg 3, everything in the `cricket` schema
# --------------------------------------------------------------------------- #
DATABASES = {
    "default": {
        **env.db(
            "DATABASE_URL",
            default="postgres://postgres:postgres@localhost:5432/cricket",
        ),
        "ENGINE": "django.db.backends.postgresql",
        "CONN_MAX_AGE": env("DB_CONN_MAX_AGE"),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            # Unqualified names resolve to `cricket` first, then `public`.
            "options": f"-c search_path={DB_SCHEMA},public",
            "sslmode": env("DB_SSLMODE"),
        },
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- #
# Identity resolution thresholds (IDENTITY_RESOLUTION.md §2)
# --------------------------------------------------------------------------- #
#: A unique candidate scoring at or above this is linked without review.
IDENTITY_MATCH_THRESHOLD = 0.95
#: New stubs with a candidate at or above this score are flagged for steward
#: review (PlayerDuplicateCandidate rows); never auto-merged.
DUPLICATE_CANDIDATE_THRESHOLD = 0.60
#: Cricsheet register ``key_*`` columns imported into ProviderEntityMap rows by
#: ``import_cricsheet_register``. Values here are external identifiers the
#: platform records alongside the Cricsheet identifier. key_cricbuzz /
#: key_cricinfo are excluded by default pending the Phase 0 licensing review.
CRICSHEET_KEY_ALLOWLIST = ["key_opta", "key_pulse"]

# --------------------------------------------------------------------------- #
# Cache / Celery
# --------------------------------------------------------------------------- #
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "cricket",
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 270
CELERY_TASK_DEFAULT_RETRY_DELAY = 5
CELERY_TASK_ANNOTATIONS = {
    "*": {
        "autoretry_for": (Exception,),
        "retry_backoff": True,
        "retry_backoff_max": 600,
        "retry_jitter": True,
        # Bounded attempts for transient failures (DATA_INGESTION §4.3).
        "max_retries": 8,
    }
}

# --------------------------------------------------------------------------- #
# DRF — JSON only, custom envelope + error handler, throttling, allow-lists
# --------------------------------------------------------------------------- #
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
    ],
    "EXCEPTION_HANDLER": "apps.core.errors.drf_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.CursorPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON"),
        "user": env("THROTTLE_USER"),
    },
    # Explicit, allow-listed backends. Views must declare `ordering_fields`
    # and/or `search_fields`; nothing is sortable/searchable by default.
    "DEFAULT_FILTER_BACKENDS": [
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# --------------------------------------------------------------------------- #
# Logging — structured JSON with request-id + redaction
# --------------------------------------------------------------------------- #
LOG_LEVEL = env("DJANGO_LOG_LEVEL")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "apps.core.logging.JsonFormatter"},
    },
    "filters": {
        "request_id": {"()": "apps.core.logging.RequestIDLogFilter"},
        "redaction": {"()": "apps.core.logging.RedactionFilter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id", "redaction"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# --------------------------------------------------------------------------- #
# i18n / static / misc
# --------------------------------------------------------------------------- #
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"


def _fail(message: str) -> None:
    """Raise a Django configuration error with a clear message."""
    raise ImproperlyConfigured(message)


def validate_production_settings(*, secret_key: str, debug: bool, allowed_hosts: list[str]) -> None:
    """Refuse to run in production with an unsafe configuration.

    Called by ``config.settings.prod`` at import time, so an insecure deploy
    fails fast instead of starting silently. Kept side-effect free (arguments
    in, exception out) so it can be unit-tested directly.
    """
    if debug:
        _fail("DJANGO_DEBUG must be false when running config.settings.prod.")
    if secret_key == DEV_INSECURE_SECRET_KEY or len(secret_key) < 32:
        _fail("DJANGO_SECRET_KEY must be a strong, non-default value in production.")
    if not allowed_hosts:
        _fail("DJANGO_ALLOWED_HOSTS must list at least one host in production.")

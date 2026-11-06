"""Base Django settings shared by every environment.

All runtime configuration comes from the environment via ``django-environ``;
``.env.example`` documents every variable. ``dev`` / ``prod`` / ``test`` import
``*`` from here and override the few things that differ.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import environ
from django.core.exceptions import ImproperlyConfigured


def _url_with_db(url: str, db: int) -> str:
    """Return ``url`` pointing at Redis logical database ``db``."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment))


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
    # Phase D live-ingestion flags (roadmap discipline: everything behind a flag).
    LIVE_INGESTION_ENABLED=(bool, False),
    LIVE_PUSH_RECEIVER_ENABLED=(bool, False),
    ENABLE_METRICS_ENDPOINT=(bool, False),
    LIVE_PUSH_HMAC_SECRET=(str, ""),
    LIVE_SHADOW_VERIFY_EVERY=(int, 10),
    LIVE_POLL_INTERVAL_SECONDS=(int, 30),
    LIVE_POLL_LOCK_TTL_SECONDS=(int, 300),
    LIVE_FRESHNESS_STALE_SECONDS=(int, 60),
    LIVE_EVENT_RETENTION_HOURS=(int, 24),
    LIVE_RECONCILIATION_WINDOW_HOURS=(int, 24),
    LIVE_BREAKER_FAILURE_THRESHOLD=(int, 5),
    LIVE_BREAKER_RECOVERY_SECONDS=(int, 60),
    LIVE_BREAKER_SUCCESS_THRESHOLD=(int, 2),
    LIVE_PUSH_REPLAY_WINDOW_SECONDS=(int, 300),
    DJANGO_ENVIRONMENT=(str, "dev"),
)

# Read a local .env when present. Never committed (see .gitignore).
environ.Env.read_env(BASE_DIR / ".env")

DEV_INSECURE_SECRET_KEY = "insecure-dev-key-change-me"

SECRET_KEY = env("DJANGO_SECRET_KEY", default=DEV_INSECURE_SECRET_KEY)
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS")

# --------------------------------------------------------------------------- #
# Phase D — live ingestion flags (LIVE_INGESTION.md documents each one).
# Everything is off by default; a deploy opts in per provider and surface.
# --------------------------------------------------------------------------- #
LIVE_INGESTION_ENABLED = env("LIVE_INGESTION_ENABLED")
LIVE_PUSH_RECEIVER_ENABLED = env("LIVE_PUSH_RECEIVER_ENABLED")
# Internal Prometheus exposition endpoint (ops surface, never public; gated at
# URL registration so a disabled deployment 404s the path entirely).
ENABLE_METRICS_ENDPOINT = env("ENABLE_METRICS_ENDPOINT")
# Shared secret for the push receiver's HMAC signatures. Empty = receiver 503s.
LIVE_PUSH_HMAC_SECRET = env("LIVE_PUSH_HMAC_SECRET")
# Shadow verification cadence: every N appended batches, and always at innings
# end (see apps.live.services.apply / verify).
LIVE_SHADOW_VERIFY_EVERY = env("LIVE_SHADOW_VERIFY_EVERY")
# Polling cadence while a match is IN_PROGRESS; breaks/terminal are slower
# (apps.live.services.polling.cadence_for).
LIVE_POLL_INTERVAL_SECONDS = env("LIVE_POLL_INTERVAL_SECONDS")
# Redis lock TTL for per-match poll de-duplication (correctness of writes comes
# from the Postgres per-match advisory lock, not from this).
LIVE_POLL_LOCK_TTL_SECONDS = env("LIVE_POLL_LOCK_TTL_SECONDS")
# Freshness threshold: a live match older than this is served with
# ``meta.stale=true`` (API_SPECIFICATION §3.1).
LIVE_FRESHNESS_STALE_SECONDS = env("LIVE_FRESHNESS_STALE_SECONDS")
# Post-completion event-log retention window (prune_events).
LIVE_EVENT_RETENTION_HOURS = env("LIVE_EVENT_RETENTION_HOURS")
# Post-completion reconciliation window (cache policy: immutable after this).
LIVE_RECONCILIATION_WINDOW_HOURS = env("LIVE_RECONCILIATION_WINDOW_HOURS")
# Per-provider-endpoint circuit breaker thresholds (breaker.py).
LIVE_BREAKER_FAILURE_THRESHOLD = env("LIVE_BREAKER_FAILURE_THRESHOLD")
LIVE_BREAKER_RECOVERY_SECONDS = env("LIVE_BREAKER_RECOVERY_SECONDS")
LIVE_BREAKER_SUCCESS_THRESHOLD = env("LIVE_BREAKER_SUCCESS_THRESHOLD")
# Push receiver: HMAC replay window (seconds).
LIVE_PUSH_REPLAY_WINDOW_SECONDS = env("LIVE_PUSH_REPLAY_WINDOW_SECONDS")

# Deployment environment name ("dev"/"test"/"prod"). The ReplayAdapter is
# hard-gated to non-prod (see apps.providers.adapters.replay).
ENVIRONMENT = env("DJANGO_ENVIRONMENT")

# --------------------------------------------------------------------------- #
# Licensing policy (SECURITY §11: configuration, not code)
# --------------------------------------------------------------------------- #
#: Per-provider content policy. A licence change is a config change, never a
#: code change. ``replay`` and ``fixture`` are the internal fake providers; a
#: licensed provider adds an entry here (post Phase-0 gate). Absent entries
#: fall back to the default policy (display allowed, commentary/derived feeds
#: and rankings not licensed).
LICENSED_CONTENT_POLICIES = {
    "replay": {
        "display_rights": True,
        "redistribution_rights": False,
        "attribution_required": True,
        "attribution": "Replay (internal test corpus)",
        "commentary_allowed": False,
        "derived_feeds_allowed": False,
        "ball_data_retention_days": None,
        "notes": "Internal fake provider; data is never redistributed.",
    },
    # The fixture-backed rankings adapter (Group 5) serves this provider's
    # recorded in-repo corpus; rankings are licensed for the internal provider.
    "fixture": {
        "display_rights": True,
        "redistribution_rights": False,
        "attribution_required": True,
        "attribution": "Fixture (internal rankings corpus)",
        "commentary_allowed": False,
        "derived_feeds_allowed": False,
        "rankings_allowed": True,
        "ball_data_retention_days": None,
        "notes": "Internal recorded rankings corpus; never redistributed.",
    },
}

# Recorded rankings corpus for the fixture-backed adapter (Group 5): in-repo
# YAML snapshots keyed per gender (``men.yaml``/``women.yaml``), never a live
# feed. Overridable so a deployment can point at its own recorded corpus.
RANKINGS_FIXTURE_DIR = BASE_DIR / "tests" / "fixtures" / "rankings"

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
    "apps.rankings",
    "apps.search",
    "apps.ingestion",
    "apps.synchronization",
    "apps.live",
    "apps.realtime",
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
# One base URL; each logical role gets its own database by default so an
# `allkeys-lru` cache eviction can never drop a Celery broker task or a rate
# limit counter (Phase E task 1; docs/REALTIME_AND_CACHING.md).
REDIS_URL = env("REDIS_URL")
CACHE_REDIS_URL = env("CACHE_REDIS_URL", default=REDIS_URL)
PUBSUB_REDIS_URL = env("PUBSUB_REDIS_URL", default=_url_with_db(REDIS_URL, 3))
RATELIMIT_REDIS_URL = env("RATELIMIT_REDIS_URL", default=_url_with_db(REDIS_URL, 4))
# Bounded client timeouts: a slow Redis degrades a feature, never a request.
REDIS_SOCKET_TIMEOUT_SECONDS = env.float("REDIS_SOCKET_TIMEOUT_SECONDS", default=0.25)
REDIS_CONNECT_TIMEOUT_SECONDS = env.float("REDIS_CONNECT_TIMEOUT_SECONDS", default=0.5)
REDIS_HEALTH_CHECK_INTERVAL_SECONDS = env.int("REDIS_HEALTH_CHECK_INTERVAL_SECONDS", default=30)
REDIS_MAX_CONNECTIONS = env.int("REDIS_MAX_CONNECTIONS", default=100)
# Header carrying a response's surrogate keys (CDN purge tags). Configurable
# because CDN providers allow renaming it; see docs/REALTIME_AND_CACHING.md.
CACHE_SURROGATE_HEADER = env("CACHE_SURROGATE_HEADER", default="Surrogate-Key")
# Number of trusted proxies between the internet and the origin (SECURITY §2
# trust model). The rightmost that many ``X-Forwarded-For`` entries are trusted
# as added by our edge; 0 = ignore the header entirely.
TRUSTED_PROXY_HOPS = env.int("TRUSTED_PROXY_HOPS", default=0)
# CDN purge endpoint (``PURGE`` by surrogate key). Empty = no CDN: origin
# invalidation is handled by the version-bump registry and this hook is a no-op.
CACHE_PURGE_URL = env("CACHE_PURGE_URL", default="")

# Token-bucket throttling (API_SPEC §7): scope -> (tokens/second, burst).
# ``deliveries`` (ball-level) is stricter than the generous default; the
# SSE/WS connect endpoints add their own connection caps (Groups 7/8).
RATELIMIT_RATES = {
    "default": (10.0, 120.0),
    "deliveries": (2.0, 20.0),
}
# Backend for the bucket store: ``redis`` (the ``ratelimit`` role) or
# ``in_process`` (tests; also the degraded-mode fallback at runtime).
RATELIMIT_BACKEND = env("RATELIMIT_BACKEND", default="redis")
# Optional per-API-key buckets: the header to read the key from. Empty disables
# the key hook entirely (no key issuance/user accounts in scope).
RATELIMIT_KEY_HEADER = env("RATELIMIT_KEY_HEADER", default="")

# --- Realtime transports (SSE/WS, API_SPEC §3.8) ---------------------------
# SSE heartbeat: a comment line every N seconds keeps proxies from closing an
# idle one-way scoreboard stream.
REALTIME_SSE_HEARTBEAT_SECONDS = env("REALTIME_SSE_HEARTBEAT_SECONDS", default=15)
# Cross-origin streams are rejected unless the Origin matches the request host
# or is listed here (comma-separated; browser clients always send Origin).
REALTIME_ALLOWED_ORIGINS = env("REALTIME_ALLOWED_ORIGINS", default="")
# Per-node connection caps (ConnectionLimiter is per process, fail-open): a
# single IP cannot monopolize a node, a hot match cannot starve other matches,
# and the node has a hard bound so memory stays bounded.
REALTIME_CONNECTION_CAP_PER_IP = env("REALTIME_CONNECTION_CAP_PER_IP", default=8)
REALTIME_CONNECTION_CAP_PER_MATCH = env("REALTIME_CONNECTION_CAP_PER_MATCH", default=2000)
REALTIME_CONNECTION_CAP_PER_NODE = env("REALTIME_CONNECTION_CAP_PER_NODE", default=10000)

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": CACHE_REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "cricket",
    },
    # Phase E uses the `cache` alias explicitly; tests swap both aliases to
    # LocMem so the cache suite needs no Redis.
    "cache": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": CACHE_REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "cricket",
    },
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
    # DRF reserves `?format=` (URL_FORMAT_OVERRIDE) for renderer selection,
    # but the public API documents `format` as a match filter (API_SPEC §3.1).
    # Move the renderer override to a non-colliding query param; renderer
    # choice stays on the Accept header in practice.
    "URL_FORMAT_OVERRIDE": "renderer",
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

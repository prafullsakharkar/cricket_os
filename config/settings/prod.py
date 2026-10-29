"""Production settings.

Refuses to start unless DEBUG is off, a real SECRET_KEY is set, and
ALLOWED_HOSTS is non-empty. All other configuration is identical to base.
"""

from __future__ import annotations

from .base import *
from .base import ALLOWED_HOSTS, DEBUG, SECRET_KEY, validate_production_settings

# --- Hard guards (fail fast, never silently insecure) ---------------------- #
validate_production_settings(
    secret_key=SECRET_KEY,
    debug=DEBUG,
    allowed_hosts=ALLOWED_HOSTS,
)

# --- Security hardening ---------------------------------------------------- #
DEBUG = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

"""Settings guards: production refuses unsafe configuration, DB uses the schema."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.base import DEV_INSECURE_SECRET_KEY, validate_production_settings

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
STRONG_KEY = "x" * 48


def test_prod_guard_rejects_debug_on() -> None:
    with pytest.raises(ImproperlyConfigured):
        validate_production_settings(secret_key=STRONG_KEY, debug=True, allowed_hosts=["a"])


def test_prod_guard_rejects_default_secret_key() -> None:
    with pytest.raises(ImproperlyConfigured):
        validate_production_settings(
            secret_key=DEV_INSECURE_SECRET_KEY, debug=False, allowed_hosts=["a"]
        )


def test_prod_guard_rejects_short_secret_key() -> None:
    with pytest.raises(ImproperlyConfigured):
        validate_production_settings(secret_key="short", debug=False, allowed_hosts=["a"])


def test_prod_guard_rejects_empty_allowed_hosts() -> None:
    with pytest.raises(ImproperlyConfigured):
        validate_production_settings(secret_key=STRONG_KEY, debug=False, allowed_hosts=[])


def test_prod_guard_accepts_safe_configuration() -> None:
    validate_production_settings(secret_key=STRONG_KEY, debug=False, allowed_hosts=["example.com"])


def test_database_connection_uses_the_cricket_schema() -> None:
    from django.conf import settings

    options = settings.DATABASES["default"]["OPTIONS"]["options"]
    assert "search_path=cricket,public" in options


def _import_prod(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "import config.settings.prod"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_prod_module_refuses_to_start_with_debug_enabled() -> None:
    env = {
        **os.environ,
        "DJANGO_DEBUG": "true",
        "DJANGO_SECRET_KEY": STRONG_KEY,
        "DJANGO_ALLOWED_HOSTS": "example.com",
    }
    result = _import_prod(env)
    assert result.returncode != 0
    assert "DJANGO_DEBUG" in (result.stderr + result.stdout)


def test_prod_module_refuses_to_start_with_the_default_secret_key() -> None:
    env = {
        **os.environ,
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": DEV_INSECURE_SECRET_KEY,
        "DJANGO_ALLOWED_HOSTS": "example.com",
    }
    result = _import_prod(env)
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in (result.stderr + result.stdout)

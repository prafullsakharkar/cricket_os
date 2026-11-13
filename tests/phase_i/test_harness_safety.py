"""Phase I Task 5 (§9.1, gate G3): every load/chaos/DAST harness refuses
non-allow-listed targets.

The ``TARGET_ALLOWLIST`` setting (comma-separated ``host:port`` entries) is the
single source of truth; it is **empty by default**, so every harness refuses
every target until an operator explicitly lists one. Production-looking hosts
are refused just like anything else — the refusal must be explicit and
auditable, and ``docs/STAGING_ACCESS.md`` is the only document allowed to name
a real environment's target (checked in the doc gate suite, not here).

``--dry-run`` never needs an allow-list entry: it validates inputs and prints
the plan without touching the network (see each harness).
"""

from __future__ import annotations

from unittest import mock

import django.conf
import pytest
from django.test import override_settings

from loadtests.safety import TargetNotAllowed, allow_list, assert_target_allowed

LOCAL = "localhost:8000"
ANOTHER_LOCAL = "127.0.0.1:9000"


def test_default_empty_allow_list_refuses_every_target() -> None:
    """Empty ``TARGET_ALLOWLIST`` -> every target refused, with a clear message."""
    assert allow_list() == set()
    with pytest.raises(TargetNotAllowed, match=r"not on the explicit allow-list"):
        assert_target_allowed(LOCAL)
    with pytest.raises(TargetNotAllowed):
        assert_target_allowed("127.0.0.1:55435")


def test_listed_target_is_allowed_others_refused() -> None:
    with override_settings(TARGET_ALLOWLIST=f"{LOCAL},{ANOTHER_LOCAL}"):
        assert allow_list() == {LOCAL, ANOTHER_LOCAL}
        assert_target_allowed(LOCAL)  # no exception
        assert_target_allowed(ANOTHER_LOCAL)
        with pytest.raises(TargetNotAllowed):
            assert_target_allowed("localhost:9999")


def test_production_looking_targets_are_refused_unless_listed() -> None:
    with override_settings(TARGET_ALLOWLIST=LOCAL):
        for prod in (
            "api.example.com:443",
            "staging.example.com:8443",
            "10.0.0.5:5432",
            "0.0.0.0:8000",
        ):
            with pytest.raises(TargetNotAllowed, match=r"api|staging|10\.0\.0\.5|0\.0\.0\.0"):
                assert_target_allowed(prod)


def test_explicit_production_listing_is_honoured() -> None:
    """The gate is explicit-and-auditable, not magic: a listed host runs."""
    with override_settings(TARGET_ALLOWLIST=f"{LOCAL},api.example.com:443"):
        assert_target_allowed("api.example.com:443")


def test_error_message_names_the_target_and_the_allow_list() -> None:
    with override_settings(TARGET_ALLOWLIST=LOCAL):
        with pytest.raises(TargetNotAllowed) as exc_info:
            assert_target_allowed("nope.example:9999")
    message = str(exc_info.value)
    assert "nope.example:9999" in message
    assert "localhost:8000" in message
    assert "refusing to run" in message


def test_env_var_fallback_when_django_is_not_configured() -> None:
    """Standalone harness runs read ``TARGET_ALLOWLIST`` from the env var."""

    class _Unconfigured:
        configured = False

    with mock.patch.object(django.conf, "settings", _Unconfigured()), mock.patch.dict(
        "os.environ", {"TARGET_ALLOWLIST": LOCAL}, clear=False
    ):
        assert allow_list() == {LOCAL}
        assert_target_allowed(LOCAL)
        with pytest.raises(TargetNotAllowed):
            assert_target_allowed("localhost:9999")

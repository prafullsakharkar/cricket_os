"""Phase I Task 2: shadow-mode gate — nothing runs when PROVIDER_SHADOW_MODE is off.

The management command, the beat task and the driver all refuse when the flag
is off, so shadow comparisons can never accidentally run in a deployment that
did not opt in.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

pytestmark = pytest.mark.django_db


def test_shadow_report_command_refuses_when_mode_off() -> None:
    with (
        override_settings(PROVIDER_SHADOW_MODE=False),
        pytest.raises(CommandError, match="shadow mode is off"),
    ):
        call_command("shadow_report", "--day", "2026-11-13")


def test_shadow_report_command_runs_when_mode_on(tmp_path) -> None:
    with override_settings(
        PROVIDER_SHADOW_MODE=True,
        SHADOW_REPORT_DIR=str(tmp_path),
    ):
        call_command("shadow_report", "--day", "2026-11-13")
    assert (tmp_path / "2026-11-13.md").is_file()


def test_compare_all_task_refuses_when_mode_off() -> None:
    from apps.shadow.tasks import compare_all

    with (
        override_settings(PROVIDER_SHADOW_MODE=False),
        pytest.raises(RuntimeError, match="shadow mode is off"),
    ):
        compare_all()


def test_driver_refuses_when_mode_off() -> None:
    from apps.shadow.services.driver import run_shadow_pass

    with (
        override_settings(PROVIDER_SHADOW_MODE=False),
        pytest.raises(RuntimeError, match="shadow mode is off"),
    ):
        run_shadow_pass()


def test_shadow_mode_defaults_to_off() -> None:
    from django.conf import settings

    assert settings.PROVIDER_SHADOW_MODE is False

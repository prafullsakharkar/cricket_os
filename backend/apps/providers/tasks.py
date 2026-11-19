"""Celery tasks for providers: licensing retention (SECURITY §11, RSK-04)."""

from __future__ import annotations

from typing import Any

from config.celery import app


@app.task(name="providers.enforce_retention", autoretry_for=())  # type: ignore[untyped-decorator]
def enforce_retention_task(
    *, dry_run: bool = True, provider_code: str | None = None
) -> list[dict[str, Any]]:
    """Enforce per-provider ball-data retention limits (dry-run by default).

    Wire ``dry_run=False`` to the beat schedule only after a steward has
    reviewed the dry-run report.
    """
    from apps.providers.licensing import enforce_retention

    reports = enforce_retention(provider_code, dry_run=dry_run)
    return [
        {
            "provider_code": report.provider_code,
            "retention_days": report.retention_days,
            "candidates": report.candidates,
            "deleted": report.deleted,
            "dry_run": report.dry_run,
        }
        for report in reports
    ]


__all__ = ["enforce_retention_task"]

"""Beat task: the shadow comparison pass (Phase I Task 2)."""

from __future__ import annotations

from config.celery import app


@app.task(name="shadow.compare_all")  # type: ignore[untyped-decorator]
def compare_all() -> dict[str, int]:
    """Run the shadow pass for today; returns the matches compared."""
    from apps.shadow.services.driver import run_shadow_pass

    return {"matches_compared": run_shadow_pass()}

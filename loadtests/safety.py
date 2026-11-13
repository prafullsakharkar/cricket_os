"""Gate G3: the explicit target allow-list for load/chaos/DAST harnesses.

Every harness (``loadtests/harness_*.py``, ``loadtests/soak.py``) calls
:func:`assert_target_allowed` **before touching the network**. The allow-list
is ``TARGET_ALLOWLIST`` (comma-separated ``host:port`` entries) and is empty by
default, so a harness refuses every target until an operator explicitly lists
one. There is no "trusted by default" class of host: production-looking targets
are refused exactly like anything else, and ``docs/STAGING_ACCESS.md`` is the
only document allowed to name a real environment's target.

``--dry-run`` never needs an allow-list entry: dry runs validate inputs and
print the plan without making a request.
"""

from __future__ import annotations

import os


class TargetNotAllowed(RuntimeError):
    """Raised when a harness target is not on the explicit allow-list."""


def allow_list() -> set[str]:
    """The explicit allow-list as ``{host:port, ...}`` (whitespace-trimmed).

    The Django setting wins when the application is configured (so tests and
    ``django-admin`` contexts use ``override_settings``); a standalone harness
    run falls back to the ``TARGET_ALLOWLIST`` env var. Both read the same
    comma-separated format with the same empty default.
    """
    return {entry.strip() for entry in _raw_allow_list().split(",") if entry.strip()}


def _raw_allow_list() -> str:
    try:
        from django.conf import settings

        if settings.configured:
            return str(getattr(settings, "TARGET_ALLOWLIST", ""))
    except Exception:
        pass
    return os.environ.get("TARGET_ALLOWLIST", "")


def assert_target_allowed(target: str) -> None:
    """Raise :class:`TargetNotAllowed` unless ``target`` is explicitly listed.

    The message names the refused target, the refusal reason and the current
    allow-list so an operator can see exactly what to add and where.
    """
    allowed = allow_list()
    if target in allowed:
        return
    listed = ", ".join(sorted(allowed)) if allowed else "<empty>"
    raise TargetNotAllowed(
        f"target {target!r} is not on the explicit allow-list (TARGET_ALLOWLIST); "
        f"refusing to run. Allowed targets: {listed}. "
        f"Add the target to TARGET_ALLOWLIST to run this harness. "
        f"Production-looking hosts are refused unless explicitly listed."
    )


__all__ = ["TargetNotAllowed", "allow_list", "assert_target_allowed"]

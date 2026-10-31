"""Pure Cricsheet register importer (no Django, no DB — stdlib only).

The management command reads CSV registers and pushes their rows through
:func:`import_register`, which drives a :class:`RegisterSink`. Splitting the
two keeps every rule about *what a register means* (row shape, bad-row
handling, per-kind behaviour) unit-testable without a database; the Django
side lives entirely in the sink and the command.

A register row is a ``Mapping[str, str]`` as produced by ``csv.DictReader``.
Three register kinds are understood:

- ``people``: ``identifier, name, unique_name`` plus ``key_*`` columns →
  ``sink.player(...)``;
- ``names``: ``identifier, name`` (alternate name variants) →
  ``sink.player_name_variant(...)``;
- ``venues``: ``identifier, name`` plus ``key_*`` columns →
  ``sink.venue(...)`` (venue keys are not imported by default — the venue
  register only carries ``key_cricinfo``, which is UX-study-only).

Bad rows (missing ``identifier`` or ``name``) and rows the sink rejects are
counted in the report's ``errors`` list, never raised. Real ``ImportJob`` /
DLQ tables belong to Phase C ``ingestion``, which will wrap this function.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

#: The three register kinds the importer understands.
Kind = Literal["people", "names", "venues"]


@dataclass
class ImportReport:
    """Counts + per-row errors from one :func:`import_register` run."""

    read: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """Number of rows that produced an error entry."""
        return len(self.errors)


class RegisterSink(Protocol):
    """Write-side interface driven by :func:`import_register`.

    Each method upserts one entity and returns a status string — ``"created"``,
    ``"updated"`` or ``"skipped"`` — that the importer counts. ``keys`` carries
    the row's non-empty ``key_*`` columns; whether they are stored is up to the
    sink (the Cricsheet allowlist is a Django-settings concern).
    """

    def player(
        self,
        identifier: str,
        name: str,
        unique_name: str | None,
        keys: Mapping[str, str],
    ) -> str: ...

    def player_name_variant(self, identifier: str, name: str) -> str: ...

    def venue(
        self,
        identifier: str,
        name: str,
        unique_name: str | None,
        keys: Mapping[str, str],
    ) -> str: ...


def _key_columns(row: Mapping[str, str]) -> dict[str, str]:
    """The row's non-empty ``key_*`` columns (the only keys the sink sees)."""
    return {k: v for k, v in row.items() if k.startswith("key_") and v}


def import_register(
    rows: Iterable[Mapping[str, str]],
    sink: RegisterSink,
    *,
    kind: Kind,
) -> ImportReport:
    """Import one register through ``sink``; returns a tally of the run.

    Rows are 1-based (like a CSV); ``errors`` entries carry ``{"row": N,
    "reason": str}``. A row missing ``identifier`` or ``name`` is an error;
    anything the sink rejects (``ValueError``) is an error too. Nothing here
    touches a database.
    """
    if kind not in ("people", "names", "venues"):
        raise ValueError(f"unknown register kind: {kind!r}")

    report = ImportReport()
    for number, row in enumerate(rows, start=1):
        report.read += 1
        identifier = str(row.get("identifier") or "").strip()
        name = str(row.get("name") or "").strip()
        if not identifier or not name:
            report.errors.append({"row": number, "reason": "missing identifier or name"})
            continue
        unique_name = (row.get("unique_name") or "").strip() or None
        keys = _key_columns(row)
        try:
            if kind == "people":
                status = sink.player(identifier, name, unique_name, keys)
            elif kind == "names":
                status = sink.player_name_variant(identifier, name)
            else:  # kind == "venues"
                status = sink.venue(identifier, name, unique_name, keys)
        except ValueError as exc:
            report.errors.append({"row": number, "reason": str(exc)})
            continue
        if status == "created":
            report.created += 1
        elif status == "updated":
            report.updated += 1
        else:
            report.skipped += 1
    return report


__all__ = ["ImportReport", "RegisterSink", "import_register"]

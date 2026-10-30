"""Licensed DLS boundary: the platform stores the target, never computes it.

SCORING_RULES §2.13: an unlicensed self-computation is never permitted. This
module exposes only enough surface for Phase C adapters to hand over a
provider/licensed value; any attempt to derive one raises
:class:`~apps.scoring.errors.LicensedMethodRequired`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NoReturn, Protocol

from apps.scoring.errors import LicensedMethodRequired, MissingDlsTargetError
from apps.scoring.types import InningsContext


class DlsSource(StrEnum):
    PROVIDER = "provider"
    LICENSED = "licensed"


@dataclass(frozen=True, slots=True)
class DlsTarget:
    """A target/par score supplied by a provider or a licensed engine."""

    target: int
    method_version: str
    source: DlsSource = DlsSource.PROVIDER
    par_score: int | None = None
    balls: int | None = None

    def __post_init__(self) -> None:
        if self.target < 0:
            raise MissingDlsTargetError("DLS target must be >= 0")
        if not self.method_version:
            raise MissingDlsTargetError("DLS method version is required")
        if self.par_score is not None and self.par_score < 0:
            raise MissingDlsTargetError("DLS par score must be >= 0")
        if self.balls is not None and self.balls < 0:
            raise MissingDlsTargetError("DLS ball count must be >= 0")
        if not isinstance(self.source, DlsSource):
            raise MissingDlsTargetError("DLS source must be provider or licensed")


@dataclass(frozen=True, slots=True)
class DlsRequest:
    """Inputs a licensed implementation would need (kept for provider calls)."""

    innings_id: str
    runs: int
    wickets: int
    legal_balls: int
    balls_remaining: int
    resources_remaining: float
    method_version: str


class DlsProvider(Protocol):
    """A provider/licensed DLS implementation; never the platform itself."""

    method_version: str

    def target_for(self, request: DlsRequest) -> DlsTarget: ...


def from_provider(
    target: int,
    method_version: str,
    *,
    par_score: int | None = None,
    balls: int | None = None,
) -> DlsTarget:
    """Accept a provider-supplied target verbatim."""

    return DlsTarget(
        target=target,
        method_version=method_version,
        source=DlsSource.PROVIDER,
        par_score=par_score,
        balls=balls,
    )


def from_licensed(
    target: int,
    method_version: str,
    *,
    par_score: int | None = None,
    balls: int | None = None,
) -> DlsTarget:
    """Accept a value from a contracted licensed engine."""

    return DlsTarget(
        target=target,
        method_version=method_version,
        source=DlsSource.LICENSED,
        par_score=par_score,
        balls=balls,
    )


def require_target(target: DlsTarget | None, *, innings_id: str | None = None) -> DlsTarget:
    if target is None:
        raise MissingDlsTargetError(
            "DLS target must be supplied by the provider/licensed source",
            innings_id=innings_id,
        )
    return target


def compute_target(*_args: object, **_kwargs: object) -> NoReturn:
    """Forbidden: the platform never computes DLS itself."""

    raise LicensedMethodRequired("DLS")


def apply_target(context: InningsContext, target: DlsTarget) -> InningsContext:
    """Return a copy of ``context`` carrying the supplied DLS target."""

    return replace(
        context,
        dls_target=target.target,
        target=target.target,
        target_balls=target.balls,
    )

"""Licensed DLS boundary: accept provider values, never compute."""

from __future__ import annotations

import pytest
from tests.scoring.helpers import innings, legal_run

from apps.scoring.dls import (
    DlsRequest,
    DlsSource,
    DlsTarget,
    apply_target,
    compute_target,
    from_licensed,
    from_provider,
    require_target,
)
from apps.scoring.errors import LicensedMethodRequired, MissingDlsTargetError


def test_provider_and_licensed_targets() -> None:
    provider = from_provider(250, "dls-2024", par_score=240, balls=300)
    assert provider.target == 250
    assert provider.source is DlsSource.PROVIDER
    assert provider.par_score == 240
    licensed = from_licensed(251, "dls-2024")
    assert licensed.source is DlsSource.LICENSED


def test_require_target_rejects_missing() -> None:
    target = from_provider(200, "v")
    assert require_target(target) is target
    with pytest.raises(MissingDlsTargetError):
        require_target(None, innings_id="i1")


def test_compute_is_forbidden() -> None:
    with pytest.raises(LicensedMethodRequired):
        compute_target(100, 2, 30)
    with pytest.raises(LicensedMethodRequired):
        compute_target()


def test_apply_target_updates_the_innings_context() -> None:
    context = innings([legal_run(1)])
    target = from_provider(200, "dls-2024", par_score=190, balls=240)
    updated = apply_target(context, target)
    assert updated.dls_target == 200
    assert updated.target == 200
    assert updated.target_balls == 240
    assert context.dls_target is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": -1, "method_version": "v"},
        {"target": 1, "method_version": ""},
        {"target": 1, "method_version": "v", "par_score": -1},
        {"target": 1, "method_version": "v", "balls": -1},
        {"target": 1, "method_version": "v", "source": "provider"},
    ],
)
def test_dls_target_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(MissingDlsTargetError):
        DlsTarget(**kwargs)  # type: ignore[arg-type]


def test_provider_protocol_is_structural() -> None:
    class Provider:
        method_version = "dls-2024"

        def target_for(self, request: DlsRequest) -> DlsTarget:
            assert request.innings_id == "i1"
            return from_provider(199, self.method_version)

    request = DlsRequest("i1", 100, 2, 60, 240, 80.0, "dls-2024")
    assert Provider().target_for(request).target == 199

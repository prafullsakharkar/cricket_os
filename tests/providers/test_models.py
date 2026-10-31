"""Provider registry models + adapter registry (DATABASE_SCHEMA §3.8)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.providers.models import DataProvider, ProviderEntityMap

pytestmark = pytest.mark.django_db

PROVIDER_UUID = "55555555-5555-7555-8555-555555555555"


#: The seed migration already inserts code="cricsheet"; tests use a distinct code.
TEST_PROVIDER_CODE = "test-provider"


def make_provider(**overrides: object) -> DataProvider:
    defaults: dict[str, object] = {"name": "Test Provider", "code": TEST_PROVIDER_CODE}
    defaults.update(overrides)
    return DataProvider.objects.create(**defaults)  # type: ignore[arg-type]


def make_map(provider: DataProvider, **overrides: object) -> ProviderEntityMap:
    defaults: dict[str, object] = {
        "provider": provider,
        "entity_type": "player",
        "internal_id": PROVIDER_UUID,
        "provider_entity_id": "opt-123",
    }
    defaults.update(overrides)
    return ProviderEntityMap.objects.create(**defaults)  # type: ignore[arg-type]


class TestDataProvider:
    def test_duplicate_code_rejected(self) -> None:
        make_provider()
        with pytest.raises(IntegrityError):
            make_provider()

    def test_credential_ref_is_plain_text_reference(self) -> None:
        provider = make_provider(credential_ref="vault:path/to/cred#cricsheet")
        assert provider.credential_ref == "vault:path/to/cred#cricsheet"


class TestProviderEntityMap:
    def test_ux_pem_rejects_duplicate_triple(self) -> None:
        provider = make_provider()
        make_map(provider)
        with pytest.raises(IntegrityError):
            make_map(provider)

    def test_same_provider_entity_id_other_entity_type_allowed(self) -> None:
        provider = make_provider()
        make_map(provider)
        make_map(provider, entity_type="team", internal_id="66666666-6666-7666-8666-666666666666")
        assert provider.entity_maps.count() == 2

    def test_entity_type_check_constraint(self) -> None:
        provider = make_provider()
        with pytest.raises(IntegrityError):
            make_map(provider, entity_type="umpire")

    def test_internal_id_is_polymorphic_plain_uuid(self) -> None:
        provider = make_provider()
        mapping = make_map(
            provider, entity_type="series", internal_id="77777777-7777-7777-8777-777777777777"
        )
        assert mapping.internal_id == "77777777-7777-7777-8777-777777777777"

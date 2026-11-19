"""Provider-registry selectors (get_provider / get_or_create_provider)."""

from __future__ import annotations

import pytest

from apps.core.errors import NotFound
from apps.providers.models import DataProvider
from apps.providers.selectors import get_or_create_provider, get_provider

pytestmark = pytest.mark.django_db

TEST_PROVIDER_CODE = "test-provider"


class TestGetProvider:
    def test_finds_active_provider(self) -> None:
        provider = DataProvider.objects.create(name="Test Provider", code=TEST_PROVIDER_CODE)
        assert get_provider(TEST_PROVIDER_CODE) == provider

    def test_missing_provider_raises_not_found(self) -> None:
        with pytest.raises(NotFound):
            get_provider("no-such-provider")

    def test_inactive_provider_is_not_found(self) -> None:
        DataProvider.objects.create(
            name="Retired Provider", code=TEST_PROVIDER_CODE, is_active=False
        )
        with pytest.raises(NotFound):
            get_provider(TEST_PROVIDER_CODE)


class TestGetOrCreateProvider:
    def test_creates_when_missing(self) -> None:
        provider = get_or_create_provider(TEST_PROVIDER_CODE, name="Test Provider")
        assert provider.code == TEST_PROVIDER_CODE
        assert provider.name == "Test Provider"
        # The seed migration already inserts cricsheet; only our code is new.
        assert DataProvider.objects.filter(code=TEST_PROVIDER_CODE).count() == 1

    def test_returns_existing_when_present(self) -> None:
        existing = DataProvider.objects.create(name="Original Name", code=TEST_PROVIDER_CODE)
        provider = get_or_create_provider(TEST_PROVIDER_CODE, name="Ignored Name")
        assert provider.pk == existing.pk
        assert provider.name == "Original Name"  # defaults not applied on hit
        assert DataProvider.objects.filter(code=TEST_PROVIDER_CODE).count() == 1

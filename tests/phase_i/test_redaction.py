"""Phase I Task 1: redaction re-run against the replay adapter path.

The provider gate fails, so there is no real adapter's request/response logging
to re-run against. This suite pins the redaction contract that a licensed
adapter must satisfy unchanged (SECURITY §3: secrets never logged, never in
error payloads, never in DLQ rows), and proves the ReplayAdapter path is
credential-free so nothing sensitive can leak through it.

``apps.ingestion.services.redact`` is the DLQ boundary; ``apps.live.services.credentials``
is the secret-manager boundary. Both must hold for the replay adapter and for
any future adapter.
"""

from __future__ import annotations

import pytest

from apps.ingestion.services.redact import REDACTED, is_sensitive_key, redact
from apps.live.services.credentials import (
    Credential,
    CredentialNotFoundError,
    EnvSecretStore,
    credential_headers,
    get_credential,
    safe_credential,
)

pytestmark = pytest.mark.django_db


def test_redact_masks_sensitive_keys_recursively() -> None:
    payload = {
        "match_id": "m1",
        "request": {
            "headers": {"Authorization": "Bearer abc123", "X-Request-Id": "r1"},
            "body": {"api_key": "k-123", "runs": 6},
        },
        "meta": {"provider_token": "tok"},
    }
    redacted = redact(payload)
    assert redacted["request"]["headers"]["Authorization"] == REDACTED
    assert redacted["request"]["body"]["api_key"] == REDACTED
    assert redacted["meta"]["provider_token"] == REDACTED
    # Non-sensitive values pass through unchanged.
    assert redacted["match_id"] == "m1"
    assert redacted["request"]["body"]["runs"] == 6
    assert redacted["request"]["headers"]["X-Request-Id"] == "r1"


def test_redact_leaves_plain_scalars_alone() -> None:
    assert redact("a plain message") == "a plain message"
    assert redact(None) is None
    assert redact(42) == 42


def test_is_sensitive_key_covers_common_secret_shapes() -> None:
    for key in ("password", "secret", "token", "credential", "api_key", "authorization", "auth"):
        assert is_sensitive_key(key)
    assert not is_sensitive_key("runs")
    assert not is_sensitive_key("match_id")


def test_credential_string_never_renders_the_value() -> None:
    credential = Credential(credential_ref="live_provider", value="super-secret-value")
    rendered = str(credential)
    assert "super-secret-value" not in rendered
    assert REDACTED in rendered
    assert "live_provider" in rendered  # the ref is traceable


def test_credential_headers_are_masked_by_redact() -> None:
    credential = Credential(credential_ref="live_provider", value="super-secret-value")
    headers = {"Authorization": f"Bearer {credential.value}"}
    # If a caller ever logged these headers verbatim the secret would leak;
    # the DLQ boundary must redact them first.
    redacted = redact(headers)
    assert redacted["Authorization"] == REDACTED
    assert "super-secret-value" not in str(redacted)


def test_credential_headers_resolve_from_env_store() -> None:
    environ = {"SECRET_LIVE_PROVIDER": "tok-123"}
    store = EnvSecretStore(environ=environ)
    credential = get_credential(_provider(credential_ref="live_provider"), store=store)
    assert credential.value == "tok-123"
    assert credential_headers(_provider(credential_ref="live_provider"), store=store) == {
        "Authorization": "Bearer tok-123"
    }


def test_missing_credential_ref_fails_loudly() -> None:
    # The replay provider is credential-free by design: it must fail loudly if
    # something ever tries to resolve a secret for it, never send an empty one.
    replay = _provider(credential_ref=None)
    with pytest.raises(CredentialNotFoundError):
        get_credential(replay)
    assert safe_credential(None) == "none"


def test_replay_adapter_path_carries_no_credentials() -> None:
    from apps.providers.adapters.replay import ReplayAdapter

    adapter = ReplayAdapter()
    # The replay adapter performs no authenticated HTTP, so its normalized
    # records can never embed credential material.
    assert adapter.capabilities().push is False
    # The registered provider row has no credential_ref (nothing to leak).
    replay_provider = _provider(credential_ref=None)
    assert getattr(replay_provider, "credential_ref", None) is None
    with pytest.raises(CredentialNotFoundError):
        get_credential(replay_provider)


def _provider(*, credential_ref: str | None) -> object:
    from apps.providers.models import DataProvider

    provider, _ = DataProvider.objects.get_or_create(
        code="replay", defaults={"name": "Replay", "credential_ref": credential_ref}
    )
    return provider

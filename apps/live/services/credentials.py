"""Provider credential abstraction (SECURITY_AND_RELIABILITY §3).

Only ``data_provider.credential_ref`` is ever stored. Secrets are read **at
call time** from a secret-manager abstraction (the env-var-backed
:class:`EnvSecretStore` is the local implementation; a real deployment swaps in
a Secrets Manager store with the same shape). Rotation therefore needs no
redeploy — the next poll reads the current secret.

Redaction is enforced at the boundaries: credential values never enter logs,
error payloads or DLQ rows. ``apps.ingestion.services.redact`` walks any
payload and masks sensitive keys; this module exports a convenience wrapper and
tests that a credential is masked wherever it would be serialised.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from apps.ingestion.services.redact import REDACTED, redact


class CredentialNotFoundError(Exception):
    """No secret is available for a provider's ``credential_ref``."""


@dataclass(frozen=True)
class Credential:
    """A resolved secret plus the reference that produced it (for tracing)."""

    credential_ref: str
    value: str

    def __str__(self) -> str:
        # Never render the secret.
        return f"Credential(ref={self.credential_ref!r}, value={REDACTED})"


class SecretStore:
    """Protocol shape: ``get_secret(credential_ref: str) -> str``."""

    def get_secret(self, credential_ref: str) -> str:
        raise NotImplementedError


class EnvSecretStore(SecretStore):
    """Reads secrets from environment variables ``SECRET_<REF>``.

    ``credential_ref`` is normalised to upper snake case. A missing variable
    raises :class:`CredentialNotFoundError` (a provider marked active without
    a credential must fail loudly, never silently send nothing).
    """

    prefix = "SECRET_"

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def get_secret(self, credential_ref: str) -> str:
        key = f"{self.prefix}{credential_ref.upper().replace('-', '_')}"
        value = self._environ.get(key)
        if value is None:
            raise CredentialNotFoundError(f"no secret for credential_ref {credential_ref!r}")
        return value


def get_credential(
    provider: Any,
    *,
    store: SecretStore | None = None,
    environ: dict[str, str] | None = None,
) -> Credential:
    """Resolve the runtime secret for a provider at call time."""
    credential_ref = getattr(provider, "credential_ref", None)
    if not credential_ref:
        raise CredentialNotFoundError(
            f"provider {getattr(provider, 'code', provider)!r} has no credential_ref"
        )
    store = store or EnvSecretStore(environ=environ)
    return Credential(credential_ref=credential_ref, value=store.get_secret(credential_ref))


def credential_headers(provider: Any, **kwargs: Any) -> dict[str, str]:
    """HTTP headers for the provider's ``auth_kind`` (Bearer by default).

    The secret is resolved at call time and used immediately; it is never
    stored, logged or echoed back by the caller.
    """
    credential = get_credential(provider, **kwargs)
    auth_kind = (getattr(provider, "auth_kind", None) or "bearer").lower()
    if auth_kind == "basic":
        import base64

        token = base64.b64encode(credential.value.encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {"Authorization": f"Bearer {credential.value}"}


def safe_credential(credential: Credential | None) -> str:
    """A redacted, log-safe rendering of a credential reference."""
    if credential is None:
        return "none"
    return str(credential)


__all__ = [
    "Credential",
    "CredentialNotFoundError",
    "EnvSecretStore",
    "SecretStore",
    "credential_headers",
    "get_credential",
    "redact",
    "safe_credential",
]

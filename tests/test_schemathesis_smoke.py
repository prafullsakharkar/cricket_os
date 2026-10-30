"""Schemathesis smoke: the generated OpenAPI schema loads and parses.

Full per-endpoint contract tests land in the API group (tests/api/). This
proves the tooling works against the WSGI app and the schema document before
any data endpoints exist (paths is currently empty by design).
"""

from __future__ import annotations

import pytest
from schemathesis.openapi import from_wsgi

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def test_schema_loads_with_schemathesis() -> None:
    from config.wsgi import application

    schema = from_wsgi("/api/v1/schema", application)
    operations = list(schema.get_all_operations())
    # No data endpoints in Phase A core; Group 7 adds them and extends this.
    assert all(operation.path.startswith("/api/v1/") for operation in operations)

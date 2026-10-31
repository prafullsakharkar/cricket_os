"""Schemathesis smoke: the generated OpenAPI schema loads and parses.

Full per-endpoint contract tests land in the API group (tests/api/). This
proves the tooling works against the WSGI app and the schema document: every
declared operation parses cleanly and lives under ``/api/v1/``. Group 7 adds
the ten reference endpoints; Phase A had none.
"""

from __future__ import annotations

import pytest
from schemathesis.core.result import Ok
from schemathesis.openapi import from_wsgi

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def test_schema_loads_with_schemathesis() -> None:
    from config.wsgi import application

    schema = from_wsgi("/api/v1/schema", application)
    operations = list(schema.get_all_operations())
    # Each operation is a Result (Ok[APIOperation] | Err[InvalidSchema]): a
    # schema that fails to parse reports Err here (same unwrap schemathesis
    # itself uses).
    assert len(operations) >= 10
    for operation in operations:
        assert isinstance(operation, Ok), f"operation failed to parse: {operation}"
        assert operation.ok().path.startswith("/api/v1/")

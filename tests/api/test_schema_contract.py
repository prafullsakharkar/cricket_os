"""OpenAPI schema contract (Task 7.5).

The generated document must (a) be a valid OpenAPI 3.0.3 document,
(b) declare every reference endpoint, and (c) describe the real payloads:
each detail response's ``data`` and every list item is validated against the
matching component schema with jsonschema (``$ref`` resolved against the
document, OpenAPI 3.0 ``nullable`` coerced to JSON-Schema null unions).

Known limitation (D8 fallback, recorded in the Phase B report): the custom
``{"data", "meta"}`` collection envelope is not introspectable by
drf-spectacular, which documents the DRF default ``{count, next, previous,
results}`` shape for list responses instead. The real envelope shape is
asserted separately in tests/api/test_reference_api.py.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from drf_spectacular.validation import validate_schema
from jsonschema import Draft4Validator

pytestmark = [pytest.mark.django_db, pytest.mark.contract]

#: Detail endpoint (schema path template) -> component for the envelope's
#: ``data``. The template ``{id}/`` is what drf-spectacular documents; the
#: parametrized tests substitute the seeded entity id when requesting.
DETAIL_ENDPOINTS: dict[str, str] = {
    "/api/v1/teams/{id}/": "Team",
    "/api/v1/players/{id}/": "Player",
    "/api/v1/competitions/{id}/": "CompetitionDetail",
    "/api/v1/series/{id}/": "Series",
    "/api/v1/venues/{id}/": "Venue",
}

#: List endpoint -> component schema every item in ``data`` must match.
LIST_ENDPOINTS: dict[str, str] = {
    "/api/v1/teams": "Team",
    "/api/v1/players": "Player",
    "/api/v1/competitions": "Competition",
    "/api/v1/series": "Series",
    "/api/v1/venues": "Venue",
}


def _load_schema(api_client: Any) -> dict[str, Any]:
    response = api_client.get("/api/v1/schema", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    return json.loads(response.content)


def _coerce_nullable(node: Any) -> None:
    """In-place: OpenAPI 3.0 ``nullable: true`` -> JSON-Schema null union.

    jsonschema does not understand the OpenAPI ``nullable`` extension, so a
    ``{type: string, nullable: true}`` property must become
    ``{type: ["string", "null"]}`` before validation. ``oneOf``-based nullable
    properties (enum + BlankEnum/NullEnum) need no change: a non-empty value
    matches exactly one branch.
    """

    if isinstance(node, dict):
        if node.get("nullable") and "type" in node:
            node["type"] = [node["type"], "null"]
        node.pop("nullable", None)
        for value in node.values():
            _coerce_nullable(value)
    elif isinstance(node, list):
        for item in node:
            _coerce_nullable(item)


def _resolve_refs(schema: Any, doc: dict[str, Any]) -> Any:
    """Expand local ``#/components/schemas/<name>`` refs into the document.

    The generated schema only ever references its own components, so a local
    expansion is deterministic and avoids the deprecated jsonschema
    ``RefResolver`` entirely.
    """

    if isinstance(schema, dict):
        if "$ref" in schema:
            ref = schema["$ref"]
            prefix = "#/components/schemas/"
            if not ref.startswith(prefix):
                raise AssertionError(f"unsupported $ref in generated schema: {ref}")
            component = doc["components"]["schemas"][ref[len(prefix) :]]
            return _resolve_refs(component, doc)
        return {key: _resolve_refs(value, doc) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_resolve_refs(item, doc) for item in schema]
    return schema


@pytest.fixture
def schema_doc(api_client: Any) -> dict[str, Any]:
    """The raw document; drf-spectacular's validator must accept it as-is."""
    doc = _load_schema(api_client)
    validate_schema(doc)
    return doc


@pytest.fixture
def json_schema_doc(schema_doc: dict[str, Any]) -> dict[str, Any]:
    """A deep copy with ``nullable`` coerced for jsonschema validation."""
    doc = copy.deepcopy(schema_doc)
    _coerce_nullable(doc)
    return doc


def _validate_payload(payload: Any, schema_name: str, doc: dict[str, Any]) -> None:
    schema = _resolve_refs(doc["components"]["schemas"][schema_name], doc)
    Draft4Validator(schema).validate(payload)


#: URL path segment -> seed namespace attribute ("series" is invariant).
_ENTITY_BY_SEGMENT = {
    "teams": "team",
    "players": "player",
    "competitions": "competition",
    "series": "series",
    "venues": "venue",
}


def _concrete_url(template: str, seed_reference: Any) -> str:
    """Substitute the seeded entity id for ``{id}`` in a schema path template."""
    if "{id}" not in template:
        return template
    # /api/v1/<entity>/{id}/ -> ["api", "v1", "teams", "{id}"] -> "teams"
    segment = template.strip("/").split("/")[2]
    entity = _ENTITY_BY_SEGMENT[segment]
    return template.format(id=getattr(seed_reference, entity).pk)


class TestSchemaDocument:
    def test_is_valid_openapi(self, schema_doc: dict[str, Any]) -> None:
        assert schema_doc["openapi"] == "3.0.3"
        assert "info" in schema_doc
        assert "paths" in schema_doc

    def test_endpoint_matrix_declared(self, schema_doc: dict[str, Any]) -> None:
        paths = schema_doc["paths"]
        for url in (*DETAIL_ENDPOINTS, *LIST_ENDPOINTS):
            assert url in paths, f"{url} missing from the schema"
            assert "get" in paths[url], f"{url} must declare GET"

    def test_path_ids_are_uuid_typed(self, schema_doc: dict[str, Any]) -> None:
        for url in DETAIL_ENDPOINTS:
            params = schema_doc["paths"][url]["get"]["parameters"]
            assert any(p.get("name") == "id" and p["schema"]["format"] == "uuid" for p in params), (
                f"{url} must declare a uuid-typed id path parameter"
            )

    def test_components_declared(self, schema_doc: dict[str, Any]) -> None:
        schemas = schema_doc["components"]["schemas"]
        for name in (
            "Team",
            "Player",
            "Competition",
            "CompetitionDetail",
            "Series",
            "Venue",
            "CountryBrief",
            "TeamBrief",
            "VenueBrief",
            "SeasonBrief",
        ):
            assert name in schemas


class TestDetailPayloads:
    @pytest.mark.parametrize(("url", "schema_name"), DETAIL_ENDPOINTS.items())
    def test_data_matches_component(
        self,
        api_client: Any,
        seed_reference: Any,
        json_schema_doc: dict[str, Any],
        url: str,
        schema_name: str,
    ) -> None:
        response = api_client.get(_concrete_url(url, seed_reference))
        assert response.status_code == 200, url
        _validate_payload(response.json()["data"], schema_name, json_schema_doc)


class TestListItems:
    @pytest.mark.parametrize(("url", "schema_name"), LIST_ENDPOINTS.items())
    def test_items_match_component(
        self,
        api_client: Any,
        seed_reference: Any,
        json_schema_doc: dict[str, Any],
        url: str,
        schema_name: str,
    ) -> None:
        response = api_client.get(url)
        assert response.status_code == 200, url
        for item in response.json()["data"]:
            _validate_payload(item, schema_name, json_schema_doc)


__all__: list[str] = []

"""Fantasy read-API contract tests (Task 7).

The three fantasy endpoints are declared in the OpenAPI schema with uuid-typed
path params, parse cleanly under schemathesis, and their real payloads match
the generated component schemas (the same jsonschema approach as
tests/api/test_schema_contract.py; the custom ``{"data", "meta"}`` envelope's
``data`` is what the components describe).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from drf_spectacular.validation import validate_schema
from jsonschema import Draft4Validator
from schemathesis.core.result import Ok
from schemathesis.openapi import from_wsgi

from apps.fantasy.services import recompute

from .factories import add_stat, make_completed_match
from .test_api import _frozen_default_version

pytestmark = [pytest.mark.django_db, pytest.mark.contract]

#: Fantasy path template -> component schema for the envelope's ``data``.
#: (Routes follow the statistics API convention: no trailing slash.
#: drf-spectacular strips the trailing "Serializer" from component names.)
FANTASY_DETAIL: dict[str, str] = {
    "/api/v1/fantasy/matches/{id}": "MatchPoints",
}
#: List endpoint -> component every item in ``data`` must match.
FANTASY_LISTS: dict[str, str] = {
    "/api/v1/fantasy/rulesets": "RulesetList",
    "/api/v1/fantasy/points": "PointsRow",
}


def _seed() -> tuple[Any, Any]:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _ruleset, version = _frozen_default_version()
    recompute.recompute_match(match)
    return match, version


def _load_schema(api_client: Any) -> dict[str, Any]:
    response = api_client.get("/api/v1/schema", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    return json.loads(response.content)


def _coerce_nullable(node: Any) -> None:
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
    doc = _load_schema(api_client)
    validate_schema(doc)
    return doc


@pytest.fixture
def json_schema_doc(schema_doc: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(schema_doc)
    _coerce_nullable(doc)
    return doc


def _validate_payload(payload: Any, schema_name: str, doc: dict[str, Any]) -> None:
    schema = _resolve_refs(doc["components"]["schemas"][schema_name], doc)
    Draft4Validator(schema).validate(payload)


class TestSchemaDocument:
    def test_is_valid_openapi(self, schema_doc: dict[str, Any]) -> None:
        assert schema_doc["openapi"] == "3.0.3"

    def test_fantasy_endpoints_declared(self, schema_doc: dict[str, Any]) -> None:
        paths = schema_doc["paths"]
        for url in (*FANTASY_DETAIL, *FANTASY_LISTS):
            assert url in paths, f"{url} missing from the schema"
            assert "get" in paths[url], f"{url} must declare GET"

    def test_match_path_id_is_uuid_typed(self, schema_doc: dict[str, Any]) -> None:
        params = schema_doc["paths"]["/api/v1/fantasy/matches/{id}"]["get"]["parameters"]
        assert any(p.get("name") == "id" and p["schema"].get("format") == "uuid" for p in params), (
            "fantasy matches id path param must be uuid-typed"
        )

    def test_fantasy_components_declared(self, schema_doc: dict[str, Any]) -> None:
        schemas = schema_doc["components"]["schemas"]
        for name in (
            "RulesetList",
            "RulesetVersion",
            "PointRule",
            "PointsRow",
            "MatchPoints",
        ):
            assert name in schemas, f"{name} missing from components"

    def test_schemathesis_parses_fantasy_operations(self) -> None:
        from config.wsgi import application

        schema = from_wsgi("/api/v1/schema", application)
        fantasy = [
            operation
            for operation in schema.get_all_operations()
            if operation.ok().path.startswith("/api/v1/fantasy/")
        ]
        assert len(fantasy) == 4  # rulesets, ruleset version detail, points, matches
        for operation in fantasy:
            assert isinstance(operation, Ok), f"operation failed to parse: {operation}"
            assert operation.ok().method.lower() == "get"


class TestListPayloads:
    @pytest.mark.parametrize(("url", "schema_name"), FANTASY_LISTS.items())
    def test_items_match_component(
        self,
        api_client: Any,
        json_schema_doc: dict[str, Any],
        url: str,
        schema_name: str,
    ) -> None:
        match, _version = _seed()
        if url == "/api/v1/fantasy/points":
            url = f"{url}?match={match.id}"
        response = api_client.get(url)
        assert response.status_code == 200, url
        for item in response.json()["data"]:
            _validate_payload(item, schema_name, json_schema_doc)

    def test_match_data_matches_component(
        self, api_client: Any, json_schema_doc: dict[str, Any]
    ) -> None:
        match, _version = _seed()
        response = api_client.get(f"/api/v1/fantasy/matches/{match.id}")
        assert response.status_code == 200
        _validate_payload(response.json()["data"], "MatchPoints", json_schema_doc)

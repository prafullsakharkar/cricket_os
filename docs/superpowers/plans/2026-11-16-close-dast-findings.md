# Close DAST Findings F-02…F-05 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four open, low-severity DAST findings tracked in `docs/SECURITY_REVIEW.md` (F-02 undocumented error statuses, F-03 400-vs-405, F-04 non-nullable nested player fields, F-05 lenient `page_size` coercion) and re-verify with a fresh schemathesis run on the local topology.

**Architecture:** Four independent, small contract fixes plus one verification task. (1) `apps/core/errors.py` gains a `method_not_allowed` catalog code so a DRF 405 is preserved instead of being re-mapped to 400. (2) A custom drf-spectacular `AutoSchema` subclass appends the standard error responses (400/404/405/429/500) to every response-direction operation, closing ST103. (3) The two nested `PlayerSerializer` fields get `allow_null=True` so the schema declares them nullable, closing ST108. (4) `PageNumberPagination` parses `page_size` strictly (400 on non-integer / <1) and declares `minimum: 1` in the schema, closing ST201/ST202. Then `make check` + the G3-gated DAST re-run verify, and `SECURITY_REVIEW.md` flips F-02…F-05 to fixed with the measured counts.

**Tech Stack:** Django 5 + DRF, drf-spectacular 0.30.0 (`AutoSchema._get_response_bodies`, `allow_null` → `nullable: true`), pytest + Django test client, schemathesis 4.32.7 (verification only).

**Spec:** `docs/SECURITY_REVIEW.md` §4 findings table (rows F-02…F-05) — the exact findings, severities, statuses and remediations this plan implements. The plan argues from that table; executors read both.

## Global Constraints

- **No network in tests.** Behavior tests use the Django test client; the schemathesis re-run (Task 5) is against the **local topology** and its results are **UNVERIFIED for production** (gate G2 still fails) — record them that way.
- **G3 discipline:** the DAST re-run goes through `scripts/security/dast_scan.py`, which calls `assert_target_allowed` first (`TARGET_ALLOWLIST=localhost:8000`).
- **`make check` must exit 0** at the end (lint, format, mypy 315 files, import-linter, secret scan, all suites incl. `test-phasei` ≥ 95%).
- **mypy strict on `apps/`** — new code in `apps/core/schema.py` and edits to `apps/core/*.py` must type-check; `Direction` is `Literal["request", "response"]` (drf_spectacular.utils).
- **ruff**, line length 100, `ruff format` clean.
- **import-linter:** `apps.core` is a leaf — `apps/core/schema.py` must not import first-party modules.
- **Reviewable commits:** one commit per task with the message shown; no backticks in `git commit -m`.
- **Error envelope contract:** every non-2xx keeps the `{"error": {"code", "message", "details"}}` shape (`apps/core/errors.py`).
- **Do not change the response bodies** of existing endpoints; only error status mapping, schema declarations, and query-param validation.
- The `ordering`/`search` schema-parameter drift (schema declares them but the API reads `sort`/`q`/`role`/`country`/`team`) is **pre-existing and out of scope**; do not touch it. If the Task 5 re-run surfaces findings from it, characterize them precisely in `SECURITY_REVIEW.md` rather than expanding scope.

## Review Focus

Input classes / failure modes the spec implies but the four findings' tests don't directly exercise — each has a test in the owning task:

1. **Every undeclared method, not just TRACE** — PUT/PATCH/DELETE on a read-only endpoint must be 405 with the envelope, not 400. (Task 1 parametrizes TRACE, PUT, PATCH, DELETE; HEAD is deliberately excluded — DRF routes HEAD to `get`.)
2. **Standard error responses must not clobber a view's own declared responses** — the schema merge uses `setdefault` so an endpoint that later declares a custom 404/429 keeps it. (Task 2 asserts the merge helper behaviour via the standard-statuses test.)
3. **A player with a null `birth_country`/`international_team`** must validate against the generated schema — the existing seed player has both set, so the regression test creates one without them. (Task 3.)
4. **`page_size` edge values the schema calls integer**: `3.5`, empty string, `0`, `-5`, and an oversized value (clamped to max 100, not rejected). (Task 4.)
5. **The error-envelope shape on the new 405** must keep `code`/`message`/`details` and be `application/json`. (Task 1.)

---

### Task 1: F-03 — preserve 405 for undeclared methods (`method_not_allowed` code)

**Files:**
- Modify: `apps/core/errors.py` (CODE_MESSAGES, `_STATUS_TO_CODE`, `_CODE_STATUS`)
- Create: `tests/api/test_error_envelope.py`

**Interfaces:**
- Consumes: DRF `MethodNotAllowed` (raised by `APIView.http_method_not_allowed` and `CachedReadMixin.dispatch` fallback), already routed through `drf_exception_handler`.
- Produces: catalog code `method_not_allowed` → HTTP 405; the envelope `{"error": {"code": "method_not_allowed", "message": "Method not allowed", "details": {"detail": "..."}}}`. Task 2 declares `405` in the schema referencing this code's description.

- [ ] **Step 1: Write the failing test** in `tests/api/test_error_envelope.py`

```python
"""Standard error envelope + 405 method-not-allowed contract (Phase I F-03/F-02).

F-03: DRF answers undeclared HTTP methods with 405 MethodNotAllowed, but the
error handler re-mapped that to a 400 invalid_query envelope. These tests pin
the 405 so a schema-driven fuzzer (schemathesis ST204) and real clients get
the REST-correct status.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.django_db


def _error(response: Any) -> dict[str, Any]:
    return json.loads(response.content)["error"]


class TestUnsupportedMethodsReturn405:
    @pytest.mark.parametrize("method", ["TRACE", "PUT", "PATCH", "DELETE"])
    def test_undeclared_method_is_405_with_envelope(
        self, api_client: Any, method: str
    ) -> None:
        response = api_client.generic(method, "/api/v1/matches/live")
        assert response.status_code == 405, f"{method} should be 405"
        err = _error(response)
        assert err["code"] == "method_not_allowed"
        assert err["message"] == "Method not allowed"
        assert "detail" in err["details"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_error_envelope.py -q`
Expected: FAIL — `assert response.status_code == 405` gets `400` (the current re-map), `err["code"] == "invalid_query"`.

- [ ] **Step 3: Implement the minimal fix** in `apps/core/errors.py`

Add a code to `CODE_MESSAGES` (after the `"invalid_query"` line):

```python
    "method_not_allowed": "Method not allowed",
```

Change the 405 mapping in `_STATUS_TO_CODE` (line ~45):

```python
    405: "method_not_allowed",
```

Add the status to `_CODE_STATUS` (after `"invalid_query": 400`):

```python
    "method_not_allowed": 405,
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_error_envelope.py -q`
Expected: PASS (4 parametrized cases).

- [ ] **Step 5: Verify the suite is still green and commit**

```bash
uv run ruff check apps/core/errors.py tests/api/test_error_envelope.py
uv run ruff format apps/core/errors.py tests/api/test_error_envelope.py
uv run pytest tests/api/test_error_envelope.py tests/api/test_reference_api.py -q
git add apps/core/errors.py tests/api/test_error_envelope.py
git commit -m "fix(api): preserve 405 for undeclared methods instead of remapping to 400 (F-03)"
```

---

### Task 2: F-02 — declare the standard error responses in the OpenAPI schema

**Files:**
- Create: `apps/core/schema.py`
- Modify: `config/settings/base.py` (`REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"]`, line ~455)
- Test: extend `tests/api/test_error_envelope.py`

**Interfaces:**
- Consumes: drf-spectacular `AutoSchema` (0.30.0) — the override hook is `_get_response_bodies(self, direction: Literal["request", "response"]) -> dict`.
- Produces: `apps.core.schema.CricketOSSchema` — the default schema class; every operation's `responses` gains `400/404/405/429/500` (the standard error envelope) while keeping its own declared responses (`setdefault` merge).

- [ ] **Step 1: Write the failing test** (append to `tests/api/test_error_envelope.py`)

```python
def _load_schema(api_client: Any) -> dict[str, Any]:
    response = api_client.get("/api/v1/schema", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    return json.loads(response.content)


STANDARD_ERROR_STATUSES = ("400", "404", "405", "429", "500")


class TestStandardErrorResponsesDeclared:
    def test_every_operation_declares_the_standard_error_statuses(
        self, api_client: Any
    ) -> None:
        doc = _load_schema(api_client)
        operations = [
            (method, path, op)
            for path, methods in doc["paths"].items()
            for method, op in methods.items()
            if isinstance(op, dict) and "responses" in op
        ]
        assert operations, "no operations found in the schema"
        for method, path, op in operations:
            responses = op["responses"]
            for status in STANDARD_ERROR_STATUSES:
                assert status in responses, (
                    f"{method.upper()} {path} does not declare {status}"
                )

    def test_error_response_uses_the_standard_envelope(self, api_client: Any) -> None:
        doc = _load_schema(api_client)
        responses = doc["paths"]["/api/v1/matches/live"]["get"]["responses"]
        schema = responses["400"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "error" in schema["required"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_error_envelope.py::TestStandardErrorResponsesDeclared -q`
Expected: FAIL — `assert "400" in responses` (the schema only declares `200` today).

- [ ] **Step 3: Implement the fix** — create `apps/core/schema.py`

```python
"""drf-spectacular schema customisation: the standard error responses.

Every public endpoint can answer with the standard error envelope
(400 invalid_query, 404 not_found, 405 method_not_allowed, 429 rate_limited,
500 internal_error — see apps/core/errors.py). drf-spectacular only declares
the success responses by default, so a schema-driven fuzzer (schemathesis)
flags every non-2xx as an undocumented status code (F-02, ST103). This schema
class appends the standard error responses to every response-direction
operation without clobbering a view's own declared responses.
"""

from __future__ import annotations

from typing import Any, Literal

from drf_spectacular.openapi import AutoSchema

#: The ``{"error": {"code", "message", "details"}}`` envelope (apps/core/errors.py).
ERROR_ENVELOPE: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {"type": "string", "example": "invalid_query"},
                "message": {"type": "string", "example": "Invalid query parameters"},
                "details": {},
            },
        },
    },
}

#: The standard error statuses every public endpoint can return, keyed by
#: the catalog code each one carries (apps/core/errors.py).
STANDARD_ERROR_RESPONSES: dict[str, dict[str, Any]] = {
    str(status): {
        "description": description,
        "content": {"application/json": {"schema": ERROR_ENVELOPE}},
    }
    for status, description in (
        (400, "Invalid query parameters (invalid_query)"),
        (404, "Not found (not_found)"),
        (405, "Method not allowed (method_not_allowed)"),
        (429, "Rate limit exceeded (rate_limited)"),
        (500, "Internal server error (internal_error)"),
    )
}


class CricketOSSchema(AutoSchema):
    """AutoSchema that declares the standard error responses on every endpoint."""

    def _get_response_bodies(
        self, direction: Literal["request", "response"] = "response"
    ) -> dict[str, Any]:
        responses = super()._get_response_bodies(direction)
        if direction == "response":
            for status, schema in STANDARD_ERROR_RESPONSES.items():
                responses.setdefault(status, schema)
        return responses
```

Modify `config/settings/base.py` `REST_FRAMEWORK` (line ~455):

```python
    "DEFAULT_SCHEMA_CLASS": "apps.core.schema.CricketOSSchema",
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_error_envelope.py tests/api/test_schema_contract.py -q`
Expected: PASS — the new schema declares the statuses and `validate_schema` (contract test) still accepts the document.

- [ ] **Step 5: Verify and commit**

```bash
uv run ruff check apps/core/schema.py config/settings/base.py tests/api/test_error_envelope.py
uv run ruff format apps/core/schema.py tests/api/test_error_envelope.py
uv run mypy apps/core/schema.py apps/core/errors.py
git add apps/core/schema.py config/settings/base.py tests/api/test_error_envelope.py
git commit -m "feat(api): declare standard error responses in the OpenAPI schema (F-02)"
```

---

### Task 3: F-04 — declare the nested player fields nullable

**Files:**
- Modify: `apps/players/api/serializers.py` (lines 42-43)
- Modify: `tests/api/test_schema_contract.py` (`_coerce_nullable`, lines 55-73)
- Test: extend `tests/api/test_schema_contract.py` (has `schema_doc`, `json_schema_doc`, `api_client`, `seed_reference` fixtures)

**Interfaces:**
- Consumes: drf-spectacular `field.allow_null` → `nullable: true` (verified in `drf_spectacular/openapi.py:1034`).
- Produces: `Player.birth_country` / `Player.international_team` schema entries of shape `{"allOf": [{"$ref": ...}], "readOnly": true, "nullable": true}`; the contract-test nullable coercion also handles the `allOf`-with-`nullable` shape.

- [ ] **Step 1: Write the failing tests** (append to `tests/api/test_schema_contract.py`)

```python
class TestNullableNestedPlayerFields:
    def test_schema_declares_nested_player_fields_nullable(
        self, schema_doc: dict[str, Any]
    ) -> None:
        player = schema_doc["components"]["schemas"]["Player"]["properties"]
        assert player["birth_country"].get("nullable") is True
        assert player["international_team"].get("nullable") is True

    def test_player_without_country_or_team_validates(
        self,
        api_client: Any,
        seed_reference: Any,
        json_schema_doc: dict[str, Any],
    ) -> None:
        from apps.players.models import Player

        Player.objects.create(full_name="Unattached Batter", gender="M")
        response = api_client.get("/api/v1/players")
        assert response.status_code == 200
        for item in response.json()["data"]:
            _validate_payload(item, "Player", json_schema_doc)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_schema_contract.py::TestNullableNestedPlayerFields -q`
Expected: FAIL — `assert player["birth_country"].get("nullable") is True` (no `nullable` today); the validation test fails with `null is not of type "object"` on `birth_country`.

- [ ] **Step 3: Implement the fixes**

`apps/players/api/serializers.py`:

```python
    birth_country = CountryBriefSerializer(read_only=True, allow_null=True)
    international_team = TeamBriefSerializer(read_only=True, allow_null=True)
```

`tests/api/test_schema_contract.py` `_coerce_nullable` — handle the `allOf` + `nullable` shape jsonschema cannot read (a null value must match a null branch):

```python
    if isinstance(node, dict):
        if node.get("nullable"):
            if "type" in node:
                node["type"] = [node["type"], "null"]
            elif "allOf" in node:
                # {allOf: [...], nullable: true} -> anyOf: [{allOf: [...]}, null]
                inner = {"allOf": node["allOf"]}
                node.clear()
                node["anyOf"] = [inner, {"type": "null"}]
        node.pop("nullable", None)
        for value in node.values():
            _coerce_nullable(value)
    elif isinstance(node, list):
        for item in node:
            _coerce_nullable(item)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_schema_contract.py -q`
Expected: PASS — new tests pass and the existing `TestListItems`/`TestDetailPayloads`/`TestPaginatedEnvelope` still pass.

- [ ] **Step 5: Verify and commit**

```bash
uv run ruff check apps/players/api/serializers.py tests/api/test_schema_contract.py
uv run ruff format apps/players/api/serializers.py tests/api/test_schema_contract.py
git add apps/players/api/serializers.py tests/api/test_schema_contract.py
git commit -m "fix(api): declare nested player fields nullable in the schema (F-04)"
```

---

### Task 4: F-05 — strict `page_size` parsing and schema `minimum: 1`

**Files:**
- Modify: `apps/core/pagination.py` (`PageNumberPagination`, lines 167-211)
- Test: extend `tests/api/test_error_envelope.py` (behavior) and `tests/api/test_schema_contract.py` (schema)

**Interfaces:**
- Consumes: DRF `PageNumberPagination.get_schema_operation_parameters` (declares `page_size` as `{"type": "integer"}` with no bounds).
- Produces: `PageNumberPagination.get_page_size` raising `InvalidQuery` (400) for non-integer or `< 1` values (matching `CursorPagination.get_page_size`, lines 67-82); `get_schema_operation_parameters` declaring `page_size` with `minimum: 1`.

- [ ] **Step 1: Write the failing tests** (append to `tests/api/test_error_envelope.py`)

```python
class TestPageSizeStrict:
    def test_page_size_float_is_400(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get("/api/v1/teams?page_size=3.5")
        assert response.status_code == 400
        assert _error(response)["code"] == "invalid_query"

    def test_page_size_empty_string_is_400(
        self, api_client: Any, seed_reference: Any
    ) -> None:
        response = api_client.get("/api/v1/teams?page_size=")
        assert response.status_code == 400

    def test_page_size_zero_is_400(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get("/api/v1/teams?page_size=0")
        assert response.status_code == 400

    def test_page_size_negative_is_400(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get("/api/v1/teams?page_size=-5")
        assert response.status_code == 400

    def test_page_size_valid_still_works(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get("/api/v1/teams?page_size=1")
        assert response.status_code == 200
        assert response.json()["meta"]["page_size"] == 1

    def test_page_size_above_max_clamps(self, api_client: Any, seed_reference: Any) -> None:
        response = api_client.get("/api/v1/teams?page_size=9999")
        assert response.status_code == 200
        assert response.json()["meta"]["page_size"] == 100
```

And append to `tests/api/test_schema_contract.py`:

```python
class TestPageSizeSchema:
    def test_page_size_declares_minimum_one(self, schema_doc: dict[str, Any]) -> None:
        seen = False
        for methods in schema_doc["paths"].values():
            for op in methods.values():
                if not isinstance(op, dict):
                    continue
                for param in op.get("parameters", []):
                    if param.get("name") == "page_size":
                        assert param["schema"].get("minimum") == 1
                        seen = True
        assert seen, "no page_size parameter in the schema"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_error_envelope.py::TestPageSizeStrict tests/api/test_schema_contract.py::TestPageSizeSchema -q`
Expected: FAIL — float/empty/0/-5 currently return 200 (DRF falls back to the default), and the schema has no `minimum`.

- [ ] **Step 3: Implement the fix** in `apps/core/pagination.py`

Replace the `PageNumberPagination` body (keep `page_size` / `page_size_query_param` / `max_page_size` and the two existing response-schema methods) by adding:

```python
    def get_page_size(self, request: Request) -> int:
        """Resolve ``?page_size=`` strictly: integers >= 1 only.

        DRF's default silently falls back to ``page_size`` on a non-positive
        integer, accepting schema-violating values (floats, empty strings) and
        hiding typos (F-05, ST201). A non-integer or <1 value is a 400
        ``invalid_query``, matching the OpenAPI ``minimum: 1`` declared in
        :meth:`get_schema_operation_parameters`.
        """
        raw = request.query_params.get(self.page_size_query_param)
        if raw is None:
            return self.page_size
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidQuery(
                "page_size must be an integer",
                details={self.page_size_query_param: raw},
            ) from exc
        if value < 1:
            raise InvalidQuery(
                "page_size must be >= 1",
                details={self.page_size_query_param: raw},
            )
        if self.max_page_size is not None:
            return min(value, self.max_page_size)
        return value

    def get_schema_operation_parameters(self, view: Any) -> list[dict[str, Any]]:
        """Declare ``page_size`` with ``minimum: 1`` (matches :meth:`get_page_size`)."""
        params = super().get_schema_operation_parameters(view)
        for param in params:
            if param.get("name") == self.page_size_query_param:
                param.setdefault("schema", {})["minimum"] = 1
        return params
```

`InvalidQuery` and `Request` are already imported at the top of `apps/core/pagination.py` (used by `CursorPagination`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_error_envelope.py tests/api/test_schema_contract.py -q`
Expected: PASS — all new tests pass; `TestPagination` in `test_reference_api.py` (`page_size=1`) still passes.

- [ ] **Step 5: Verify and commit**

```bash
uv run ruff check apps/core/pagination.py tests/api/test_error_envelope.py tests/api/test_schema_contract.py
uv run ruff format apps/core/pagination.py tests/api/test_error_envelope.py
uv run mypy apps/core/pagination.py
uv run pytest tests/api/test_error_envelope.py tests/api/test_schema_contract.py tests/api/test_reference_api.py -q
git add apps/core/pagination.py tests/api/test_error_envelope.py tests/api/test_schema_contract.py
git commit -m "fix(api): strict page_size parsing and schema minimum: 1 (F-05)"
```

---

### Task 5: Re-verify with the DAST scan and update SECURITY_REVIEW.md

**Files:**
- Modify: `docs/SECURITY_REVIEW.md` (F-02…F-05 rows, §4; the release-candidate record, §2)
- Evidence: `reports/security/dast-<timestamp>.md` (new, from the re-run)

**Interfaces:**
- Consumes: the four fixes above; `scripts/security/dast_scan.py` (G3-gated) + the local stack.
- Produces: an updated `SECURITY_REVIEW.md` in which F-02…F-05 are **fixed** with the measured post-fix schemathesis counts (or, if residual findings remain, precise re-characterization — never rounded up).

- [ ] **Step 1: Run the full gate**

```bash
make check
```

Expected: exit 0 (lint, format, mypy, import-linter, secret scan, all suites).

- [ ] **Step 2: Start the local stack and run the DAST scan**

```bash
docker compose up -d postgres redis
nohup uv run uvicorn config.asgi:application --host 127.0.0.1 --port 8000 > /tmp/opencode/uvicorn_dast2.log 2>&1 &
sleep 9
TARGET_ALLOWLIST=localhost:8000 timeout 300 uv run python scripts/security/dast_scan.py --target localhost:8000
```

Expected: a new `reports/security/dast-<timestamp>.md`; `check-deploy` still fixed; schemathesis failures should now be **zero ST108 and ST204**, and **no ST103/ST201/ST202 on the fixed statuses/params**. Record the exact residual counts from the output (e.g. `ST103` count, any remaining `ST202`) — do not edit the numbers.

- [ ] **Step 3: Stop the server** (`pkill -f "uvicorn config.asgi"` — kill by exact pattern, not `pkill -f` on a pattern matching the shell's own command line), then update `docs/SECURITY_REVIEW.md`:

Flip F-02…F-05 rows to `fixed` in the §4 table with the re-run evidence column (`reports/security/dast-<timestamp>.md`), and update §2's release-candidate record with the new `make check` phase_i test count. If any residual finding remains, add a new row with the precise count and a one-line characterization (e.g. residual ST202 from the pre-existing `ordering`/`search` param drift — out of scope, tracked) — **do not claim zero if the scan did not measure zero**.

- [ ] **Step 4: Verify and commit**

```bash
git add docs/SECURITY_REVIEW.md reports/security/
git commit -m "docs(security): DAST re-run evidence, F-02..F-05 fixed in SECURITY_REVIEW (follow-up)"
```

---

## Self-Review

**Spec coverage (SECURITY_REVIEW §4):** F-02 → Task 2 (standard error responses); F-03 → Task 1 (405 preserved); F-04 → Task 3 (nullable nested fields); F-05 → Task 4 (strict page_size + schema minimum). Remediation notes match: F-02 "document the standard error statuses per endpoint" → the AutoSchema appends them to every endpoint; F-03 "map unknown methods to 405" → `method_not_allowed` code; F-04 "mark the field nullable" → `allow_null`; F-05 "tighten query-param coercion; align schema" → strict `get_page_size` + `minimum: 1`. Task 5 re-verifies with the scan and updates the table.

**Placeholder scan:** no TBD/TODO; every code step has concrete code; test steps have exact commands and expectations.

**Type consistency:** `method_not_allowed` appears in `CODE_MESSAGES`, `_STATUS_TO_CODE`, `_CODE_STATUS` (Task 1) and the Task 2 405 description; `get_page_size`/`get_schema_operation_parameters` names match DRF's overrides; `_coerce_nullable` keeps its existing signature. `InvalidQuery` and `Request` are already imported in `pagination.py`.

**Review Focus:** (1) parametrized methods in Task 1; (2) `setdefault` merge + the every-operation test in Task 2; (3) the null-player validation test in Task 3; (4) the edge-value tests in Task 4; (5) the envelope assertions in Task 1.

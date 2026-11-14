# API deprecation policy

Versioning, change-freeze and deprecation rules for the public API
(`docs/CRICKET_API_SPECIFICATION.md`). The policy is **additive-only**: a
launched endpoint never has a field or behaviour removed without the full
deprecation lifecycle below.

## 1. Change-freeze for launch

- From the **launch candidate onward** (the final Phase I commit), no breaking
  API change ships without the deprecation lifecycle in §2.
- "Breaking" = removing/renaming a field, changing an envelope shape, changing
  a status/error code, or changing pagination/filtering semantics.
- Additive changes (new endpoints, new optional fields, new query params,
  new error codes) ship freely but must stay backward-compatible with the
  golden payloads (`tests/api/golden/`).

## 2. Deprecation lifecycle

1. **Deprecate** — register the endpoint in `apps/core/deprecation.py`
   (`DeprecationMeta(endpoint=<view_name>, sunset_at=<ISO date>,
   replacement=<successor>)`). Every response starts carrying:

   ```
   Sunset: 2027-02-14
   Link: </api/v2/matches/{id}>; rel="successor-version"
   ```

2. **Minimum window** — PROPOSED: **2 releases or 90 days** from the
   deprecation date to the `sunset_at` date, whichever is longer. The window
   must appear in the changelog/release notes.
3. **Sunset** — after `sunset_at` the endpoint answers `410 Gone` with the
   RFC 7807-style error body `{"error": {"code": "gone", ...}}`. The `Sunset`
   header is no longer emitted (the endpoint no longer exists).
4. **Removal** — the view and its route are deleted only after `410` has been
   served for at least one full release cycle (operators need the window to
   migrate).

## 3. `Sunset` / `Link` header semantics

- `Sunset` carries the ISO-8601 date the endpoint stops being served. Clients
  must treat a missing `Sunset` header as "no scheduled removal".
- `Link` with `rel="successor-version"` names the replacement endpoint. It may
  be an absolute or relative URL. A deprecated endpoint with no successor
  omits `Link`.
- Both headers are **additive**: they never change the response body, status,
  or caching semantics of the endpoint they annotate.

## 4. Additive-only compatibility rule

- Existing golden payloads (`tests/api/golden/*.json`) and the OpenAPI
  contract (`tests/api/test_schema_contract.py`) must keep passing for every
  release in the deprecation window — a deprecation never rewrites a live
  payload shape.
- New fields are added to the schema and the payload **in the same release**,
  so a client that tolerates unknown fields (the documented contract) keeps
  working.

## 5. Mechanism status (Phase I)

- The mechanism (`apps/core/deprecation.py` + middleware) ships with a
  test-only deprecated endpoint proving the headers, the `410 Gone` path and
  additive-only compatibility (`tests/phase_i/test_deprecation.py`).
- **No live endpoint is deprecated in Phase I** — the registry is empty in
  production configuration.

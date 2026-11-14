# Security review (Phase I Task 8)

Status: **current** — last updated 2026-11-13 for the Phase I release candidate.

This document records the Phase I security validation: the CI security gates
(§12.1), the DAST-equivalent scan of the local topology (§12.2) and the
re-verification of the `SECURITY §12` threat-model controls with evidence
(§12.3). It is a living document; every change to the security posture updates
it.

Gate context: **G1 (provider docs) and G2 (staging docs) both FAIL** — there is
no vendor adapter, no network, no staging environment. Everything here is
measured on the **local single-node topology** and is **UNVERIFIED for
production** unless stated otherwise. **No penetration test has occurred.**

---

## 1. CI security gates (§12.1)

| Gate | Target | Status | Evidence |
|---|---|---|---|
| `make lint` (ruff) | clean | ✅ pass | `make check` runs it; see §2 |
| `make typecheck` (mypy) | 0 errors | ✅ pass | `make check` runs it |
| `make imports` (import-linter) | boundaries hold | ✅ pass | `make check` runs it |
| `make test` + phase suites | all green | ✅ pass | `make check` runs them |
| `make test-secrets` (secret scan) | **new in this task** | ✅ pass | `scripts/security/scan_secrets.py`; clean on the tree |
| `make test-security` (aggregate) | **new in this task** | ✅ pass | 112 tests, see §3 |

The secret scan (`make test-secrets`) greps the tree for real-credential
patterns — AWS access/secret keys, GitHub tokens/PATs, Slack tokens, Stripe
live keys, Google API keys, OpenAI keys, private-key blocks and non-local DB
URLs carrying passwords — and **fails on hits**. `.env` files (the real secrets
live there, gitignored) and binary/serialized artifacts are excluded;
`.env.example` is scanned (placeholders only). The documented local dev URLs
(`postgres:postgres@localhost:…`) are not flagged. Pattern inventory:
`python scripts/security/scan_secrets.py --list`.

## 2. Release-candidate verification

`make check` against the final Phase I commit (Task 8 commit) passes end to
end: lint + format clean, mypy clean (315 source files), import-linter clean,
and the full test suite green (core + Phase B/C + scoring + identity + phase C
+ phase F + phase H + **phase I**: 171 tests, 96.12% line+branch ≥ 95% on the
shadow-mode modules).

## 3. Security-behaviour suites (re-verified, §12.3)

| Control | Tests | Result (re-run 2026-11-13) |
|---|---|---|
| `/metrics` not public — gated at URL registration (`ENABLE_METRICS_ENDPOINT`); 404 when off, registered when on | `tests/phase_d/test_observability.py::test_metrics_endpoint_is_gated_by_setting` (+ 3) | ✅ 4 passed |
| Push receiver auth — HMAC-SHA256 signature + replay window; 404 when `LIVE_PUSH_RECEIVER_ENABLED` off; 503 without secret; 429 flood | `tests/phase_d/test_ingestion_framework.py` (receiver tests incl. `test_push_receiver_*`) | ✅ 29 passed |
| Realtime origin checks — cross-origin rejected (4403), same-origin + allow-list allowed | `tests/phase_e/test_realtime_transport.py::test_cross_origin_stream_is_rejected`, `::test_same_origin_and_allowlisted_streams_are_allowed` (+16) | ✅ 18 passed |
| Realtime connection caps — per-IP cap, spoofed `X-Forwarded-For` cannot bypass | `tests/phase_e/test_realtime_transport.py::test_per_ip_connection_cap_*`, `::test_spoofed_forwarded_for_*` | ✅ (in the 18) |
| Realtime throttling — token bucket, 429 + `Retry-After`, cache hits throttled too | `tests/phase_e/test_throttling.py` | ✅ 18 passed |
| WebSocket consumer origin/caps at the transport | `tests/phase_e/test_realtime_websocket.py` | ✅ 22 passed |
| DAST gate + secret-scan gate | `tests/phase_i/test_security.py` (6) + `tests/api/test_schema_contract.py` (15) | ✅ 21 passed |
| **Aggregate** | `make test-security` | ✅ **112 passed** |

## 4. DAST-equivalent scan (§12.2) — local topology, G3-gated

`scripts/security/dast_scan.py` calls `assert_target_allowed` (gate G3) before
any request and runs the documented equivalent scanners — **OWASP ZAP is NOT
present in this environment** and is recorded NOT-RUN; **no penetration test
is claimed**. The equivalent scanners are real and installed:

* **Django `check --deploy` against `config.settings.prod`** (configuration
  hardening) — passes with **zero `security.*` issues**.
* **schemathesis** (schema-driven fuzzer, already a dev dependency) against
  the app's OpenAPI schema — the unauthenticated pass over the public
  surface.
* **authenticated pass** — a real Django admin login (`/admin/`) driven with a
  provisioned local steward account, probing the ops surfaces.

Full run: `reports/security/dast-20261113T201150Z.md` (target
`localhost:8000`, uvicorn + Postgres + Redis local stack).

### Findings (tracked)

| # | Finding | Severity | Status | Remediation | Owner |
|---|---|---|---|---|---|
| F-01 | **Pagination-envelope schema drift**: drf-spectacular declared the DRF default `{count, results}` (or a bare array) while list endpoints emit `{data, meta}` — ST108 on every list endpoint | medium (contract) | **fixed** in this commit | `get_paginated_response_schema` added to `apps/core/pagination.py` (both cursor + page-number); contract locked by `tests/api/test_schema_contract.py::TestPaginatedEnvelope` | API |
| F-02 | ST103 undocumented status codes (~30): e.g. 404/400/429 on list endpoints not declared in the schema | low | open (tracked) | drf-spectacular response-status completion; document the standard error statuses per endpoint | API |
| F-03 | ST204 undeclared methods return `400` instead of `405` (TRACE etc. on read-only endpoints) — requests are still rejected | low | open (tracked) | map unknown methods to `405` in the API exception handler (REST contract) | API |
| F-04 | ST108 residual (1–4): a `Player` list item carries a null nested field the schema declares non-nullable | low | open (tracked) | mark the field `nullable` in the serializer/schema | API |
| F-05 | ST201/ST202 (2–5): `page_size` as a float/null accepted (lenient coercion); a few schema-valid requests rejected | low | open (tracked) | tighten query-param coercion; align schema | API |
| F-06 | **Admin path IP restriction — GAP**: no IP-restriction middleware for `/admin` | medium (design gap) | **open** | Proposed control: reverse-proxy allow-list at the edge (deployment platform). Out of scope to build the edge in this phase. **UNVERIFIED for production** | Platform |
| F-07 | **WAF rules**: none present locally (no real CDN/WAF in the local topology) | medium (design gap) | open / **UNVERIFIED** | Depends on the deployment platform; §9 spike mitigations reference WAF/CDN as design, not evidence | Platform |
| F-08 | External penetration test | — | **planned, not run** | See `docs/PENTEST_SCOPE.md` | Platform |

Interpretation of the schemathesis ST codes: all are **schema/runtime contract
drift** (undocumented status codes, undeclared methods, envelope/type
mismatches), not exploitable vulnerabilities — every non-conforming request is
rejected (400/404/429). No auth bypass, injection, access-control violation or
data exposure was found on the local topology. The one client-visible contract
bug (the pagination envelope drift, F-01) is **fixed and locked by a test**.

## 5. Threat-model controls (SECURITY §12) — status

| Threat | Mitigation | Phase I status |
|---|---|---|
| Credential theft | Secret manager, no secrets in DB/logs, rotation | Mechanism verified (credential rotation game-day drill 20261113T161035Z; `make test-secrets` clean — no real credentials in the repo) |
| Data scraping / abuse | Rate limits, WAF, CDN, bot management | Rate limiting + caps verified locally; **WAF/CDN UNVERIFIED** (F-07, depends on platform) |
| Tampering with scores | Read-only public API; audited admin corrections; derived-from-source scoring | Verified locally (admin correction audit, wrong-score game-day drill) |
| Provider outage cascade | Circuit breakers, cache fallback, `stale` marking | Verified locally (provider-outage + Redis-failover drills) |
| Insider misuse | RBAC + append-only audit + least privilege | RBAC/audit verified locally; **admin IP restriction is a GAP** (F-06) |
| DoS on marquee match | CDN absorption, coalesced realtime, throttling | Throttling/caps/reconnect-storm verified locally; **CDN absorption UNVERIFIED** (platform) |

## 6. Out of scope / honest limitations

- **No penetration test occurred.** ZAP is not installed; an external pentest
  is *planned, not run* (`docs/PENTEST_SCOPE.md`).
- All scan results are **UNVERIFIED for production** (gate G2 FAILS — local
  single-node topology only).
- The admin-session probe used a **locally provisioned** steward account that
  was created for the scan and deleted afterwards; it is not evidence of
  production access control.

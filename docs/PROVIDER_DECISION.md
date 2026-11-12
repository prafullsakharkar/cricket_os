# PROVIDER_DECISION.md — licensed live data provider

> ## Gate NOT met (as of 2026-11-12)
>
> **No licensed provider is integrated.** This document is a **placeholder and
> template**: it states the Phase I provider gate outcome and lists exactly what
> must be supplied before a vendor adapter may be written. Filling this
> document in IS the gate — `tests/test_gate_docs.py` asserts the "Gate NOT
> met" marker until a real decision lands and deliberately removes it.
>
> Consequences while the gate is unmet (Phase I brief §Gates):
> - **No vendor adapter is written, no provider API payload shape is guessed,
>   and no network is called.**
> - Shadow-mode and comparison tooling are built and validated against the
>   internal **ReplayAdapter** (`apps/providers/adapters/replay.py`), which
>   implements the same `ProviderAdapter` protocol and passes
>   `tests/contracts/provider_adapter_kit.py`.
> - Every report, `LAUNCH_CRITERIA.md` entry, `GO_NO_GO.md` row and the final
>   Phase I report states this gate outcome **prominently**.
> - Anything that would need the real provider (real corrections behaviour,
>   real schema drift, real rate limits, real coverage) is **UNVERIFIED** and
>   named as such.

---

## What a real decision must contain (template)

Fill every section below from the **signed licence terms** and the provider's
**official API documentation or recorded sample payloads** (which must be
checked into the repo — see §3). Do not leave placeholders in a merged
decision; a partial answer set is not a decision.

### 1. Signed licence terms summary

| Item | Answer required |
|---|---|
| **Provider / product / contact** | Legal entity, product name, contract ref, signed date. |
| **Display rights** | May scores/scorecards/ball-by-ball be displayed to end users? Any watermark/attribution requirement? |
| **Redistribution rights** | May data be redistributed (API/embeds/third parties)? Any limits on caching/storing? |
| **Retention** | How long may ball-by-ball/deliveries be retained after a match? Any deletion requirement? |
| **Corrections semantics** | Are revisions pushed or polled? How is a correction marked (version/revision/notice)? |
| **DLS / target data** | Is DLS/par-score supplied by the provider (`source=provider`)? Rights to display? |
| **Rankings** | Are official rankings supplied and licensed for display? |
| **Commentary rights** | Is commentary in scope? Display/redistribution limits? |
| **Rate limits** | Requests/min per endpoint, burst allowances, push vs poll. |
| **SLA** | Availability %, latency, correction latency, support hours/contacts. |
| **Attribution** | Exact text/placement required. |

### 2. Adapter-boundary readiness (what the platform already provides)

A licensed adapter must implement the existing `ProviderAdapter` protocol
(`apps/providers/base.py`) and pass `tests/contracts/provider_adapter_kit.py`
**unchanged** (unknown-enum quarantine, partial payloads, rate-limit/5xx
handling, `since_seq` resume, redaction). Phase I added
`tests/contracts/test_golden_mapping.py` (Raw → canonical golden mapping) as
the precedent for the provider's `Raw -> canonical` mapping tests.

Checklist for the future provider integration:

- [ ] Official API documentation **or recorded sample payloads** checked into
      the repo (this is the "official docs / recorded payloads in repo" gate
      requirement; no guessing, no scraping).
- [ ] `ProviderAdapter` implementation passes the contract kit unchanged.
- [ ] Golden `Raw -> canonical` mapping tests pass.
- [ ] `LicensedContentPolicy` entry added in `config/settings/base.py`
      (`LICENSED_CONTENT_POLICIES`) from the signed terms — configuration, not
      code (SECURITY §11).
- [ ] Attribution rendering verified; `403 license_required` degradation for
      unlicensed features verified; retention job dry-run verified.
- [ ] Credentials via the secret-manager abstraction only
      (`apps/live/services/credentials.py`); redaction tests re-run.
- [ ] Corrections behaviour aligned with `CRICKET_DATA_INGESTION.md`; any
      mismatch with the design assumptions documented there.
- [ ] `PROVIDER_SHADOW_MODE` ingest + `reports/shadow/` comparison evidence.
- [ ] No credentials in the repo; no network in unit/integration tests.

### 3. Official docs / sample payloads location

Recorded sample payloads (redacted where required) live under
`tests/fixtures/provider_<code>/`; official docs may be referenced by URL and
pinned in this file. Nothing here may be scraped or reverse-engineered from a
proprietary site.

---

## Current state (2026-11-12)

- **Gate: NOT met.** No provider, no signed terms, no official docs/samples.
- **Fallback in place:** ReplayAdapter-based shadow validation, identity
  continuity, reversible match de-duplication, local-topology harnesses
  (all results marked UNVERIFIED).
- **Owner of this gate:** commercial/legal + product (Phase 0 licensing gate,
  non-engineering). Engineering is ready at the adapter boundary.

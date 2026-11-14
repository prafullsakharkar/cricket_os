# Phase I — Pre-launch validation and provider cutover

Date: 2026-11-12. Status: **planning**. Branch: `phase-i` (to be created from
`develop` = `97b3564`). Predecessor:
[Phase H](2026-11-11-phase-h-fantasy.md).

This plan implements the user brief "Phase I (Pre-launch validation and
provider cutover)". Phase I is **not in the original roadmap**; it closes items
Phases C–G explicitly deferred. The planning package is `./docs` (REVISED
versions read in full — see §0). Phases A–H are merged on `develop`.

> **Read the planning docs first (this plan assumes you have):**
> `CRICKET_IMPLEMENTATION_ROADMAP.md`, `CRICKET_RISKS_AND_ASSUMPTIONS.md`
> (RSK-01,02,03,05,06,08,12), `CRICKET_DATA_INGESTION.md` (provider matrix,
> licensing checklist, reconciliation), `CRICKET_PERFORMANCE_AND_SCALING.md`
> §12, `CRICKET_SECURITY_AND_RELIABILITY.md` §9/11/12,
> `CRICKET_DATABASE_SCHEMA.md` (match unique indexes, `provider_entity_map`,
> match provenance), plus the docs that exist: `LIVE_INGESTION.md`,
> `IDENTITY_RESOLUTION.md`, `RUNBOOKS.md`, `ENDPOINT_COVERAGE.md`,
> `FRONTEND_CONTRACT.md`. Docs the brief references that are **absent**
> (`PROVIDER_DECISION`, `DEPLOYMENT_DECISION`, `STAGING_ACCESS`,
> `HISTORICAL_IMPORT`, `OPERATIONS`, `CAPACITY_MODEL`, `SECURITY_REVIEW`,
> `FEATURE_FLAGS`) are deliverables or gate-placeholders of this phase.

---

## 0. Gates — decided FIRST (these shape the whole phase)

Per the brief: "Gates (decide these first)".

### G1 — Provider gate: **FAILS**

`docs/PROVIDER_DECISION.md` is **absent** (checked 2026-11-12). There is no
signed licence summary, no provider official API documentation, and no
recorded provider sample payload in the repo.

Consequences (mandatory, per brief):
- **Do NOT write a vendor adapter, guess payload shapes, or call any network.**
- Build the shadow-mode and comparison tooling against the **ReplayAdapter**
  (`apps/providers/adapters/replay.py`), which already implements the
  `ProviderAdapter` protocol and passes `tests/contracts/provider_adapter_kit.py`.
- List exactly what the gate needs (Task 1) and say the gate is unmet
  **prominently** in every report, `LAUNCH_CRITERIA.md`, `GO_NO_GO.md` and the
  final phase report.

### G2 — Environment gate: **FAILS**

`docs/STAGING_ACCESS.md` is **absent**. There is no named production-like
staging environment, no endpoint list, no runtime credential story, no limits,
no notify list.

Consequences (mandatory, per brief):
- Deliver parameterized, dry-runnable harnesses and runbooks.
- Run them only against the **local production-topology stack** (Postgres
  `:55435/cricket`, Redis `:6379`, ASGI app — see `.env`).
- **Mark every staged result `UNVERIFIED`** and name the missing gate
  (`STAGING_ACCESS.md` + the staging env itself) wherever a number appears.

### G3 — Safety rule (always)

Never run load, chaos or security tests against production or any environment
not named in `STAGING_ACCESS.md`. **Every harness refuses to run unless an
explicit target allow-list contains the target** (env var `TARGET_ALLOWLIST`,
default empty → harness exits non-zero with a clear message). Tests prove the
refusal (Task 5/8).

---

## 1. Scope reality (what this phase becomes)

Because both gates fail, the brief's Task 1 ("real provider adapter") and parts
of Tasks 2/5/6/8 that require a licensed provider or a staging env are
**adapted**, not skipped. The phase delivers:

| Brief task | Delivered as |
|---|---|
| 1. Real provider adapter | Gate outcome stated prominently; `PROVIDER_DECISION.md` created as a gate-placeholder + exact requirement checklist; adapter-boundary readiness evidence re-run against ReplayAdapter (contract kit + golden mapping fixture test); licensing policy verified against the replay policy with dry-run evidence; corrections behaviour of the replay provider captured and aligned with DATA_INGESTION. |
| 2. Shadow mode | New `apps/shadow` comparison service that diffs our derived rows vs the provider card (ReplayAdapter `get_scorecard`) and vs the second source (the recorded historical card); `PROVIDER_SHADOW_MODE` flag; dated report under `reports/shadow/`; Prometheus metrics + alerts (contract-tested); `docs/LAUNCH_CRITERIA.md` with owners and **PROPOSED** thresholds. |
| 3. Identity continuity | Batch linking job (`link_provider_identities`) resolving a recorded live-provider entity corpus against canonical entities via Phase B `resolve()`; coverage report (auto-linked / candidates pending / stubs); steward workload estimate; continuity test (career stats identical before/after mapping; zero duplicate players, no split careers). |
| 4. Cross-provider match dedup | Ingest-time canonicalisation service (series-or-competition + both teams + start-time tolerance + venue when present) consulting `provider_entity_map(entity_type='match')`; documented source-precedence policy; `match_duplicate_candidate` steward queue; **reversible** match merge/split (audited, idempotent, digest-verified, no double counting); edge-case test matrix incl. double-headers, super-over, abandoned, timezone/date-line. |
| 5. Staged performance validation | `docs/CAPACITY_MODEL.md` created with MEASURED/ASSUMED split; parameterized allow-list-gated harnesses for all §12 scenarios on the local topology; soak (≥2 h locally) incl. a deploy and a replica failover attempt; every number tagged env/instances/command/date; per-SLO demonstrated/not-demonstrated/failed + implication. |
| 6. Game day + ops readiness | Scripted fault-injection game day on the local topology (ReplayFaults + infra faults); detection/TTM/runbook-sufficiency recorded; runbook gaps fixed in `docs/RUNBOOKS.md`; alert routing E2E to a local sink (real channel blocked — UNVERIFIED); restore drill + derived-data rebuild drill with **measured** RTO/RPO; `docs/OPERATIONS.md` operator guide + queue-volume estimate from shadow data. |
| 7. Consumer integration | Reference client across the full fallback ladder behind a real proxy config (`ops/proxy/` nginx-style) with buffering/timeouts/compression for SSE+WS; proxy-induced failures reported; **`Sunset` header implemented** (additive; the API spec already promises it — currently missing); API change-freeze note + deprecation policy; additive-only golden-payload compat test. |
| 8. Security validation | CI security gates re-run; unauthenticated + authenticated DAST (ZAP) against the local topology only, allow-list-gated; findings tracked in `docs/SECURITY_REVIEW.md` (created) with remediation table; Phase G threat-model controls re-verified with evidence (metrics gated, webhook HMAC, origin checks, admin IP restriction — the last is a documented gap); pentest scope doc produced if an external test is planned (planned, not run). |
| 9. Launch plan + go/no-go | `docs/LAUNCH_PLAN.md`, `docs/GO_NO_GO.md`, `docs/FEATURE_FLAGS.md` (created), `RISKS_AND_ASSUMPTIONS.md` updated (close/re-rate RSK-01/02/03/05/06/08/12 with evidence), roadmap Phase I section, post-launch error-budget policy + SLO review cadence + deferred backlog; final report `docs/PHASE_I_PRELAUNCH_REPORT.md`. |

**Honesty rule (brief constraint):** label everything `MEASURED`, `ASSUMED` or
`UNVERIFIED`; never claim readiness, SLO attainment or security assurance
beyond what was measured. The two failed gates are the headline of the final
report, not a footnote.

---

## 2. Worktree, branch and commit discipline

- Create the worktree with the using-git-worktrees skill: `phase-i` from
  `develop`, checked out at the current `develop` tip (`97b3564`). The plan
  file is copied into the worktree and committed as part of Task 0.
- **One reviewable commit (or commit group) per task**, in order 0→9. Each
  commit keeps `make check` green and all gates (lint/typecheck/imports/
  migration-drift/schema-ddl) green.
- Commit messages: conventional commits, no backticks (shell substitution
  hazard). Example: `feat(shadow): comparison service + dated report + metrics (Task 2)`.
- The SDD ledger lives at `.superpowers/sdd/2026-11-11-phase-i-pre-launch/progress.md`
  (gitignored, never committed) — record every decision D-I1…D-IN and every
  doc conflict as you go, mirroring the Phase H ledger.
- `develop` stays untouched until the user says merge. Do not `git checkout
  develop` in this worktree; update `develop` with `git -C <develop-dir> ...`
  when the user approves.

## 3. Conventions that carry over (do not re-learn)

- **Doc precedence:** `CRICKET_DATABASE_SCHEMA.md` wins on table structure;
  `CRICKET_BACKEND_ARCHITECTURE.md` on module boundaries. Record every conflict
  resolved in the SDD ledger and the final report.
- **Migration discipline:** every new table/column ships (a) a **reversible**
  Django migration, (b) a matching update to the reference DDL in
  `CRICKET_DATABASE_SCHEMA.md` **in the same commit** (the `migration-drift`
  gate diffs reference-DDL DB vs migrations DB — it will fail otherwise), (c)
  a forward/backward (N-1) test in `tests/test_migrations.py` following the
  existing pattern, (d) `schema-ddl` green. Contract-step (data-destructive)
  drops are not used in this phase.
- **Coverage gates:** a new `.coveragerc-phasei` + `make test-phasei` target
  gating the high-risk modules (shadow comparison, canonicalisation, merge/
  split, linking batch) — mirror `.coveragerc-phaseh` and the `test-phaseh`
  Makefile target; wire it into `make check`.
- **Metrics:** add names in `apps/core/metrics.py` only; new alerts go in a
  contract-tested file under `ops/alerts/` (pattern:
  `tests/phase_d/test_observability.py`, `tests/phase_h/test_observability.py` —
  every rule's expr names a registered metric and its `runbook` label matches a
  `docs/RUNBOOKS.md` heading).
- **DLQ/reconciliation invariant (SECURITY §9, §10):** mismatches are flagged
  and queued, **never auto-corrected**. Shadow comparisons and merge decisions
  obey this.
- **No network in unit/integration tests.** No real credentials in the repo.
  No scraping. `ENVIRONMENT=prod` blocks the ReplayAdapter — never weaken that.
- **Import boundaries:** new modules obey the import-linter contracts
  (`apps.realtime` imports only `core`; `apps.live` never imports
  `apps.ingestion` — reporters are registered by dependency inversion; a new
  `apps.shadow` must define its own contract in `.importlinter`).

---

## 4. Task 0 — Plan + gate-outcome documents (commit 0)

**Goal:** commit the plan and the two gate-placeholder documents so the gate
outcomes are reviewable and the phase has a home for evidence.

### 4.1 Files

- `docs/superpowers/plans/2026-11-11-phase-i-pre-launch.md` — this file.
- `docs/PROVIDER_DECISION.md` — **gate-placeholder + template.** State at the
  top: *"Gate NOT met as of 2026-11-12. No licensed provider is integrated."*
  Then the exact requirement checklist a future decision must fill: signed
  licence terms summarised (display, redistribution, retention, corrections
  semantics, DLS/rankings/commentary rights, rate limits, SLA), and where the
  provider's official API docs or recorded sample payloads must be placed in
  the repo. Keep it a template with empty fields, not invented answers.
- `docs/STAGING_ACCESS.md` — **gate-placeholder + template.** State the gate is
  unmet; template for endpoints, runtime credential injection, limits, notify
  list, and the allow-list contract harnesses require.

### 4.2 Tests

- `tests/test_gate_docs.py` (new, small): asserts both files exist and contain
  the string `"Gate NOT met"` — so a future PR that satisfies a gate must
  deliberately flip these documents, which is the review signal. (This also
  keeps the phase honest: the placeholders cannot silently linger.)

### 4.3 How to verify

`make check` green; `grep -c "Gate NOT met" docs/PROVIDER_DECISION.md
docs/STAGING_ACCESS.md` returns 2. Commit: `docs(phase-i): plan + gate
placeholders (Task 0)`.
---

## 5. Task 1 — Provider gate outcome + adapter-boundary readiness (commit 1)

**Brief task 1 is gate-blocked.** This task delivers what the brief's gate
section requires instead: the gate outcome stated prominently, the exact list
of what the gate needs, and evidence that the adapter boundary is ready to
accept a licensed adapter without code changes.

### 5.1 Gate outcome + requirements

- `docs/PROVIDER_DECISION.md` (from Task 0) is the living statement. This task
  adds the **adapter-boundary readiness section** to it: the `ProviderAdapter`
  protocol, the contract kit, the golden-mapping fixture, and the exact
  deliverables a provider integration must satisfy (official docs / recorded
  payloads in repo → adapter → contract kit + golden tests → licensing policy
  entry → shadow mode). This is the "list exactly what is needed" from the
  brief.

### 5.2 Golden mapping fixture test (Raw → canonical)

The ReplayAdapter's "raw" input is the recorded `delivery`/`match` rows and its
"canonical" output is the normalized `LiveInningsBatch`/`LiveState` records.
Add a **golden test** that pins this mapping so a future licensed adapter has a
precedent for `Raw -> canonical` golden tests:

- `tests/contracts/test_golden_mapping.py` (new): builds a recorded match from
  the existing Phase C fixture (`tests/fixtures/cricsheet/123456.yaml` round
  trip, or the factories used by `tests/phase_c`), runs the ReplayAdapter
  `iter_deliveries`/`get_state`/`get_scorecard`, and asserts the normalized
  records match a **golden JSON snapshot** checked into
  `tests/contracts/golden/replay_<match>.json`. Re-generate the golden only by
  explicit intent (document the command in the test docstring).
- The existing contract kit (`tests/contracts/provider_adapter_kit.py` +
  `test_replay_adapter.py`) already covers the shape contract — do not weaken
  it; the golden test is additive.

### 5.3 Licensing policy verified against the replay policy (dry-run evidence)

- Re-run the existing licensing tests (`tests/phase_d/test_licensing.py`) and
  capture evidence:
  - **Attribution rendering:** `attribution_for`/`license_meta` output for the
    `replay` policy (config in `config/settings/base.py` →
    `LICENSED_CONTENT_POLICIES["replay"]`) appears in API `meta` on list feeds.
  - **`license_required` degradation:** commentary/derived-feeds/rankings for
    the replay policy raise `403 license_required` (policy has
    `commentary_allowed=false`, `derived_feeds_allowed=false`).
  - **Retention job:** `manage.py enforce_retention --dry-run` against the
    replay policy (`ball_data_retention_days: null`) produces a dry-run report
    and writes nothing. Capture the command output into
    `reports/phase_i/task1_licensing_evidence.md` (dated).
- Add any missing assertion to `tests/phase_d/test_licensing.py` if the three
  items above are not already pinned (check first — most exist).

### 5.4 Redaction re-run

- The contract kit already covers redaction of `IngestionError.payload`. Add a
  test asserting the **ReplayAdapter's logging** redacts nothing sensitive
  (the replay provider has no credentials, so the assertion is: no
  `Authorization`/secret-shaped fields appear in captured request/response log
  lines). If `apps/live/services/credentials.py` exposes a redaction helper,
  assert it is applied on the replay path too.

### 5.5 Corrections behaviour alignment (documented)

- ReplayAdapter's corrections: served on the poll after the recorded stream is
  fully consumed, `revision=2`, matching the live pipeline's correction path
  (supersede `is_current=false` + `delivery.corrected`). Document in the
  phase report (and in `docs/PROVIDER_DECISION.md` readiness section) how this
  maps to DATA_INGESTION's corrections semantics (never destructive, revisioned,
  audited `DataCorrection` row) and that a real provider's *push-vs-poll*
  correction behaviour is UNVERIFIED until the gate is met.

### 5.6 Tests / verification

- New: `tests/contracts/test_golden_mapping.py`, redaction-logging test,
  `tests/test_gate_docs.py` (Task 0). Existing: contract kit, licensing tests.
- Run `pytest tests/contracts tests/phase_d/test_licensing.py` and `make check`.
- Commit: `feat(providers): golden mapping + licensing/redaction readiness evidence (Task 1)`.
---

## 6. Task 2 — Shadow mode (comparison service, report, metrics, criteria)

**Goal:** continuously compare what the platform derives for a match against
the provider's own scorecard (and a second source) without exposing anything
publicly, and output a dated report + Prometheus metrics + launch criteria.

### 6.1 New app `apps/shadow` (pure-ish service, no tables needed)

Create `apps/shadow/` with `AppConfig` (label `shadow`) and register in
`INSTALLED_APPS` (base + test). No models ⇒ no migration required. Add an
import-linter contract: `apps/shadow` may import `apps.core`, `apps.matches`,
`apps.innings`, `apps.providers` selectors, `apps.synchronization` models —
and must NOT import `apps.ingestion` (DLQ writer) directly; use the existing
reporter registration pattern (dependency inversion, like
`apps/live/services/verify.py`).

Files:
- `apps/shadow/compare.py` — the comparison core (pure-ish, testable):
  - `compare_match(match) -> ComparisonReport`: pulls (a) our derived Tier-1
    rows (`innings_summary`, `batting_entry`, `bowling_entry`, `match_result`,
    `extras` from innings summary), (b) the provider card via
    `ReplayAdapter.get_scorecard(provider_match_id)`, (c) the second source —
    the **recorded historical card** (the Phase C fixture card, i.e. the
    cricsheet-derived canonical card for the same match), then diffs **per
    category**:
    - `totals` (innings runs/wickets/overs), `extras`, `result` (result_type,
      winner, margin), `batting` (per-batter runs/balls/4s/6s/dismissal),
      `bowling` (per-bowler overs/maidens/runs/wickets).
  - Each diff is `ComparisonFinding(category, innings_number?, entity_key?,
    our_value, provider_value, second_source_value, severity)`.
  - **Never auto-correct.** Findings go to a queue: reuse the
    `ReconciliationJob` + redacted `IngestionError` DLQ pattern (register a
    reporter, mirror `apps/synchronization/services/reconciliation.py`), and
    additionally to the dated report + metrics. No write-back to match rows.
- `apps/shadow/metrics.py` — metric names (add to `apps/core/metrics.py`
  facade, follow the existing `metric(...)` pattern):
  - `shadow_comparisons_total{category}` (counter)
  - `shadow_mismatches_total{category,severity}` (counter)
  - `shadow_ingest_latency_seconds` (histogram: provider event time → our
    `match_event_log` row `created_at` for the same `delivery_seq`)
  - `shadow_correction_lag_seconds` (histogram: provider revision timestamp →
    our `data_correction.applied_at`), `shadow_corrections_total`
  - `shadow_unresolved_identity_total` (gauge: map rows
    `verified=False` for the shadow provider)
  - `shadow_schema_drift_total` (counter: adapter contract/validation
    quarantine events observed during shadow ingest)
  - `shadow_coverage_gaps_total` (counter: matches the provider omits or lists
    late vs the second source)
- `apps/shadow/services/report.py` — `write_report(day) -> Path`: renders a
  dated report under `reports/shadow/<YYYY-MM-DD>.md` (+ `.json` machine copy)
  with: mismatch rate by category, ingestion-latency distribution (p50/p90/p99),
  correction frequency + lag, coverage gaps, schema-drift events, unresolved
  identity rate, and the environment banner
  (`UNVERIFIED — local topology, gate STAGING_ACCESS.md unmet`). `reports/`
  is new; add it (keep the dated files committed as evidence — they are small).
- `apps/shadow/management/commands/shadow_report.py` — `manage.py shadow_report
  [--provider replay] [--day YYYY-MM-DD]`: runs comparisons for all completed
  matches ingested via the shadow provider within the day and writes the dated
  report. Gated on `PROVIDER_SHADOW_MODE` (see below); refuses to run when the
  flag is off.
- `apps/shadow/services/driver.py` — `run_shadow_pass()`: the beat-task body
  (registered as `shadow.compare_all` in `apps/shadow/tasks.py`), scheduled
  only when `PROVIDER_SHADOW_MODE=true`. It walks completed matches for the
  shadow provider, compares, records metrics, queues findings, writes the
  report.

### 6.2 Setting

- `config/settings/base.py`: `PROVIDER_SHADOW_MODE = env("PROVIDER_SHADOW_MODE")`
  (default off). Document in `LIVE_INGESTION.md`-style table (add a section to
  `docs/LIVE_INGESTION.md` or the new `docs/SHADOW_MODE.md` — create the latter,
  1 page, and link from README).
- **Public exposure guard:** shadow-ingested data must never be served
  publicly. Today the replay provider is already non-prod, so the guard is:
  (a) the replay adapter refuses `ENVIRONMENT=prod` (exists), and (b) shadow
  reports/metrics are written only when `PROVIDER_SHADOW_MODE=true`. When a
  real provider lands behind shadow mode, this section says the deployment must
  point public reads at the non-shadow namespace — record as a documented
  requirement, not implemented code (no real provider).

### 6.3 Alerts + contract test

- `ops/alerts/shadow_alerts.yml` (new): rules such as
  `ShadowMismatchRateHigh` (rate of `shadow_mismatches_total` over 15 m above a
  PROPOSED threshold), `ShadowUnresolvedIdentityHigh`, `ShadowCorrectionLagHigh`,
  `ShadowSchemaDrift`. Add the file to the observability contract test
  (`tests/phase_d/test_observability.py` style — copy the pattern: every rule's
  expr references a registered metric; every `runbook` label matches a
  `docs/RUNBOOKS.md` heading; add the new headings under a `## Shadow mode`
  section).
- Add the new metric names to the metrics registry test if one asserts the full
  registry.

### 6.4 `docs/LAUNCH_CRITERIA.md`

Quantitative exit criteria, each with: **owner**, status (`proposed` unless the
product owner supplies numbers — the brief says do not invent silently), and
evidence source. PROPOSED thresholds (mark every one `PROPOSED`):
- Zero unreconciled result mismatches over the last N completed shadow matches
  (N proposed, e.g. 50).
- Ingestion lag p99 ≤ 60 s (the §12 SLO), shadow-measured.
- DLQ for shadow stage not growing (flat over a window).
- Mismatch rate by category below proposed caps (e.g. batting ≤ 0.5%,
  totals ≤ 0.1%).
- Coverage gaps below proposed cap; unresolved identity rate below proposed cap.
Each criterion's evidence link points at `reports/shadow/*.md` + metrics.

### 6.5 Tests

- `tests/phase_i/test_shadow_compare.py`: unit tests with **injected
  mismatches of every category** (mutate a batting row, an extras figure, a
  result, a bowler line) → the right `ComparisonFinding`s appear, severities
  correct, and **no auto-correction** (match rows unchanged, a queue row +
  report entry created). Determinism: same inputs → identical report JSON
  (run twice).
- `tests/phase_i/test_shadow_report.py`: dated report written to a tmp dir
  (override `SHADOW_REPORT_DIR` via `override_settings`), correct filename,
  env banner present, JSON round-trips.
- `tests/phase_i/test_shadow_metrics.py`: metric names registered; counts bump
  on a compare pass; latency histogram recorded from a replayed match's
  `match_event_log` timestamps.
- `tests/phase_i/test_shadow_gate.py`: `shadow_report`/beat task refuse when
  `PROVIDER_SHADOW_MODE=false`.
- `tests/phase_i/test_observability.py`: shadow alert file contract (metric
  names + runbook headings).

### 6.6 How to verify

- `pytest tests/phase_i` green; `make check` green.
- Manual: `PROVIDER_SHADOW_MODE=true python manage.py shadow_report --day <today>`
  against a replay-ingested completed match produces
  `reports/shadow/<today>.md`.
- Commit (may split): `feat(shadow): comparison service, dated reports, metrics, launch criteria (Task 2)`.

---

## 7. Task 3 — Identity continuity across sources (commit 3)

**Goal:** link a live provider's players/teams/venues/series to the entities
already imported from the historical source (cricsheet), as a batch with the
steward queue as the review path, and prove a player's career statistics do not
change when the provider id is mapped.

### 7.1 The second provider namespace

The ReplayAdapter reads the *same* recorded corpus, so it cannot demonstrate a
genuinely divergent second source. To exercise the real `resolve()` machinery
across two provider namespaces on real repo data:

- New reversible seed migration `providers/0003_seed_replay_provider.py`
  (mirror `0002_seed_cricsheet_provider.py`): create `DataProvider(code="replay",
  name="Replay (live stand-in)", ...)`. Idempotent `update_or_create`, reversible
  unseed that respects the `PROTECT` map rows (same pattern as 0002).
- Recorded live-provider entity corpus: `tests/fixtures/provider_live/players.yaml`
  (+ teams/venues/series as needed) — a **recorded sample** of entity attributes
  (name, DoB, country, gender, teams) as the live provider would send. Derived
  from the in-repo `people.csv`/`names.csv` for a handful of entities; the file
  header documents the derivation and that it is a stand-in (UNVERIFIED against
  a real provider's view, which would contain its own spelling/DoB quirks).

### 7.2 Batch linking job

- `apps/providers/services/linking.py`:
  - `link_entity(provider, entity_type, provider_entity_id, attributes) -> LinkOutcome`
    wraps the existing `resolve()` (exact map hit / auto-linked ≥0.95 /
    stub created) and classifies: `auto_linked` (map row created pointing at an
    existing entity) vs `candidate_pending` (stub + `PlayerDuplicateCandidate`
    in [0.60, 0.95) for players; for teams/venues/series, a `verified=False`
    map row counts as pending) vs `stub_created`.
  - `link_all(provider) -> LinkSummary` (counts by entity type, by outcome,
    list of candidate ids) — runs inside one transaction per entity.
- `apps/providers/management/commands/link_provider_identities.py`:
  `manage.py link_provider_identities --provider replay [--entity-type player|team|venue|series] [--dry-run]`.
  Dry-run reports what *would* happen without writing.
- **Coverage report:** the command writes `reports/phase_i/identity_coverage.md`
  (dated): auto-linked, candidates pending (with steward queue links), stubs
  created, and the **steward workload estimate** (candidate count × minutes-per-
  review assumption stated explicitly, e.g. 2 min/review — ASSUMED).

### 7.3 Continuity test (the brief's Task 3 acceptance)

`tests/phase_i/test_identity_continuity.py`:
- Seed a player with a cricsheet map row + career stats (via the Phase F
  pipeline on a recorded match). Capture `player_career_stat` rows + digest.
- Run `link_entity(provider=replay, 'player', <id>, attributes)` → the map row
  points at the **existing** player (auto-linked, since attributes match).
- Assert: exactly **one** `Player` row for that person (no duplicate), career
  stats **identical** (row-for-row, digests equal), no split career, and
  re-running the link is a no-op (idempotent — same map row).
- Negative case: attributes with a DoB mismatch → stub created, pair **not**
  linked, candidate **not even flagged** (DoB cap), no duplicate career rows
  after stats re-run.
- Residual-duplicates report: assert zero duplicates in the test set; the batch
  summary reports the residual count (expected 0 in the fixture corpus).

### 7.4 Steward path

- Verify the admin queue already surfaces `PlayerDuplicateCandidate` (Phase B)
  and add `verify_mapping` usage for the auto-linked rows in the batch report
  (the steward can `verify` them; the batch leaves `verified=False` so review is
  explicit). No new admin UI needed unless a gap is found.

### 7.5 Tests / verification

- New: `tests/phase_i/test_identity_continuity.py`, `tests/phase_i/test_linking_batch.py`
  (dry-run writes nothing; outcomes classified; idempotent re-run), seed
  migration forward/backward in `tests/test_migrations.py` style.
- Run `pytest tests/phase_i` + `make check`.
- Commit: `feat(providers): identity linking batch + continuity test (Task 3)`.
---

## 8. Task 4 — Cross-provider match de-duplication (commit group 4a/4b/4c)

Deferred from Phase D; required now because recently completed matches arrive
from both sources. Canonicalisation per SECURITY §9: match on (series or
competition, both teams, start time within a configurable tolerance, venue when
present). One canonical match with multiple `provider_entity_map` entries and
an explicit source-precedence policy. **Never merge automatically below a
confidence threshold**; route to a steward review queue (reuse the Phase B
pattern). **Match merges must be reversible** (unlike player merges) — audited
split path, justified in the design and tested.

### 8.1 Source-precedence policy (documented first)

`docs/MATCH_DEDUPLICATION.md` (new): states which source is authoritative for
live, which for completed, and how conflicts are reconciled:
- **Live/ball-by-ball:** the live provider is authoritative while a match is in
  progress and for 24 h after completion (the reconciliation window).
- **Completed cards:** the historical source (cricsheet) is authoritative for
  completed cards only when the live provider does not cover the match;
  otherwise the live provider's final card wins (it is the freshest).
- **Conflicts** (result disagreement, score disagreement): flagged to the
  steward via the reconciliation/DLQ path (SECURITY §9 — never auto-corrected).
- Recorded as a decision in the SDD ledger (D-Ix) and referenced from
  `GO_NO_GO.md`.

### 8.2 Tables (migration 4a) — DATABASE_SCHEMA wins, update it in the same commit

Two new tables + one column, all **reversible** (add-only, no drops):

```sql
CREATE TABLE cricket.match_duplicate_candidate (
  id uuid PRIMARY KEY,
  candidate_match_id uuid NOT NULL REFERENCES cricket.match(id),
  canonical_match_id uuid NOT NULL REFERENCES cricket.match(id),
  score numeric(4,3) NOT NULL,
  breakdown jsonb,
  status text NOT NULL DEFAULT 'open',      -- open | resolved | rejected
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);
CREATE UNIQUE INDEX ux_match_dup_candidate
  ON cricket.match_duplicate_candidate (candidate_match_id, canonical_match_id);
CREATE INDEX ix_match_dup_canonical
  ON cricket.match_duplicate_candidate (canonical_match_id, status);

CREATE TABLE cricket.match_merge (
  id uuid PRIMARY KEY,
  merged_match_id uuid NOT NULL UNIQUE REFERENCES cricket.match(id),
  canonical_match_id uuid NOT NULL REFERENCES cricket.match(id),
  before jsonb NOT NULL,   -- exact inventory of affected child rows (per table: id + prior match_id)
  after  jsonb NOT NULL,   -- post-merge inventory (for verification and split)
  status text NOT NULL DEFAULT 'merged',  -- merged | split
  created_by uuid, created_at timestamptz NOT NULL DEFAULT now(),
  split_by uuid, split_at timestamptz
);
CREATE INDEX ix_match_merge_canonical ON cricket.match_merge (canonical_match_id);
```

- New column on `cricket.match`: `merged_into_id uuid NULL REFERENCES
  cricket.match(id)` (partial index `WHERE merged_into_id IS NOT NULL`), so a
  merged-away match redirects on reads and selectors can exclude it. The DDL
  in `CRICKET_DATABASE_SCHEMA.md` §3.5 gains the column and §3.8 gains both
  tables — **in the same commit as the migration** (migration-drift gate).
- Django models: `MatchDuplicateCandidate`, `MatchMerge` in
  `apps/matches/models.py`; `match.merged_into_id` FK self-reference. Admin:
  duplicate-candidate review actions (`confirm_match_merge`, `reject_candidate`,
  `split_match`) mirroring the player duplicate admin
  (`apps/players/admin.py`) — actions must be idempotent and audit via
  `AuditLog` (`match.merged`, `match.split`, `match.dup.reviewed` events).
- Migration tests: forward/backward in `tests/test_migrations.py` (N-1), plus
  `migration-drift` and `schema-ddl` green.

### 8.3 Canonicalisation service (ingest-time prevention — the primary control)

`apps/matches/services/canonicalise.py`:
- `canonical_key(series_id_or_competition_id, team_ids(frozenset of 2),
  start_time_utc, venue_id|None, match_number|None, match_desc|None)` — the
  matching predicate. Tolerance `MATCH_DEDUP_START_TOLERANCE_MINUTES`
  (default 180, PROPOSED) applied to `start_time_utc`; venue included only when
  both candidates have one; `match_number`/`match_desc` (when both present) act
  as tie-breakers so same-teams-twice-in-a-day double-headers do not collide.
- `score_candidate(existing_match, incoming_fixture) -> float` — 1.0 when every
  present signal agrees; partial credit when venue missing or match_number
  differs; 0.0 when teams or series/competition differ. Thresholds reuse the
  identity pattern: `MATCH_DEDUP_AUTO_THRESHOLD` (default 0.95, PROPOSED) →
  auto-canonicalise; `MATCH_DEDUP_REVIEW_THRESHOLD` (default 0.60, PROPOSED) →
  steward queue.
- `find_canonical_match(provider, fixture_attrs) -> (Match|None, decision)`:
  consulted by **fixture sync** (`apps/live/services/fixture_sync.py` /
  `apps/synchronization/services/fixtures.py`) **before** creating a `Match`.
  - exact/strong hit → create/refresh the `provider_entity_map(entity_type='match',
    provider_entity_id=<provider's id>, internal_id=<canonical match id>)` row,
    set `is_primary` per the precedence policy, and return the canonical match
    (no new `Match` row; the provider's deliveries later append into the
    canonical match through the normal `apply_batch`).
  - candidate band → create `MatchDuplicateCandidate(status='open')`
    (get_or_create, unique pair) and return the *new* stub match (never merge
    below threshold).
  - miss → caller creates a new match as today.
- Wire `apply_batch`/polling to resolve the provider match id → canonical match
  id before writing deliveries (the existing `resolve()`-style map lookup; the
  live write path already keys on the internal match — the fixture-sync step is
  the natural choke point; confirm in code and add the hook with a test).

### 8.4 Reversible merge/split service (post-hoc safety net)

`apps/matches/services/merge.py`:
- `merge_matches(merged, canonical, *, actor, reason) -> MatchMerge`:
  - Guards: neither row `merged_into_id`-linked; both exist; `merged !=
    canonical`; runs inside one transaction with `pg_advisory_xact_lock` on
    both ids (idempotent: an existing `merged` row → no-op returning it).
  - **Child re-pointing by policy:** move the merged match's child rows into
    the canonical where they fit without conflict — `match_team` (by
    `team_number`), `toss`, `match_player`, `match_official`, `match_award`,
    `innings` (by `(innings_number, is_super_over, super_over_number)` —
    **conflicting innings stay on the merged row** and are excluded from all
    selectors/stats via `merged_into_id`, see §8.5), and `provider_entity_map`
    rows (`entity_type='match'`) re-pointed to `internal_id=canonical.id` with
    `is_primary` set per the precedence policy.
  - Record `before`/`after` inventories (per table: row id + prior `match_id`)
    in the `match_merge` row, set `merged.merged_into_id = canonical.id`, bump
    `revision`, append `AuditLog(action='match.merged')`.
  - **No data is deleted** — reversibility by construction.
- `split_match(merge_row, *, actor) -> MatchMerge`:
  - Only `status='merged'` rows split. Reverse exactly the `before` inventory
    (re-point child rows to their prior match id, restore the map rows'
    `internal_id`/`is_primary`, clear `merged_into_id`), set `status='split'`,
    `split_by`/`split_at`, append `AuditLog(action='match.split')`. Idempotent:
    splitting twice or splitting a split row is an error (not a silent no-op).
- `recompute_affected_series(canonical, merged)`: after merge or split, re-run
  the Phase F Tier-2 pipeline for the affected series (existing
  `rebuild_statistics`/sweep commands) and **verify no double counting**: the
  digest-validated aggregate rows change only by the expected delta, and the
  affected matches appear exactly once. Prove idempotence: run twice, second is
  a no-op (same digests, no revision bump).

### 8.5 Selector exclusion (merged-away matches are invisible)

- `apps/matches/selectors/…` and the match feeds (`/matches/live|recent|upcoming`)
  must exclude `merged_into_id IS NOT NULL`. Add a manager/queryset helper
  `Match.objects.exclude_merged()` and apply at the selector boundary; reads of
  a merged match's id return the canonical (301 redirect, mirroring player
  merge). Innings/deliveries of the merged row are reachable only by the split
  path, never by public selectors (assert with a test).
- The statistics pipeline already recomputes from source; add the
  `exclude_merged()` to its match sweep inputs so a merged-away match's innings
  can never be double-counted (belt) on top of the innings-conflict rule
  (braces).

### 8.6 Edge-case test matrix (all in `tests/phase_i/test_match_dedup.py`)

Property tests (Hypothesis, `--hypothesis-profile=ci`):
- **Order independence:** the same duplicate arriving (a) canonical first, (b)
  duplicate first — both end with one canonical match + two map rows.
- **Idempotence:** canonicalise/merge twice ≡ once (digests equal, no revision
  bump, no extra queue rows).
- **No double counting:** after merge + Tier-2 re-run, career/team/series
  aggregates include each match exactly once (digest-verified).
- **Merge/split reversibility:** merge then split restores the exact prior state
  (child rows' `match_id`s, map rows, `merged_into_id`, revision).

Unit/regression cases:
- Duplicate arrival in both orders; timezone/date-line edge cases (start times
  on either side of UTC midnight within tolerance → same canonical key);
  rescheduled fixtures (start moved beyond tolerance → not a duplicate → queue);
  **double-headers** (same teams twice in a day → distinct when `match_number`
  present, queued when absent);
  **super-over** and **abandoned** matches (canonicalise and merge without
  corrupting `is_super_over`/`NO_RESULT` rows).
- Queue: below-threshold candidates go to `match_duplicate_candidate` and are
  never auto-merged; `reject_candidate` closes the pair.
- Split of a merge with conflicting innings restores the conflict (both innings
  back under their original match).

### 8.7 Tests / verification

- New: `tests/phase_i/test_match_dedup.py` (matrix above),
  `tests/phase_i/test_match_merge.py` (guards, reversibility, idempotence,
  selector exclusion, 301), migration tests, admin action tests.
- `make check` (incl. `test-phasei` coverage gate), `migration-drift`,
  `schema-ddl`, N-1 all green.
- Commits: (4a) migrations + models + admin + reference-DDL; (4b) canonicalise
  service + fixture-sync hook; (4c) merge/split + selector exclusion +
  stats re-run + full test matrix. Each green.
---

## 9. Task 5 — Staged performance validation (local topology, UNVERIFIED)

**Gate G2 fails**, so there is no staging env. Deliver parameterized,
dry-runnable harnesses; run against the local production-topology stack; mark
every staged result `UNVERIFIED` with the missing gate named. Do not claim SLO
attainment.

### 9.1 Safety allow-list (G3) — built FIRST

- `config/settings/base.py`: `TARGET_ALLOWLIST = env("TARGET_ALLOWLIST",
  default="")` (comma-separated host:port entries).
- `loadtests/safety.py` (new): `assert_target_allowed(target) -> None` raising
  `TargetNotAllowed` (exit non-zero, clear message) unless the target is in the
  allow-list. Every load/chaos/DAST harness calls it first.
- `tests/phase_i/test_harness_safety.py`: proves each harness refuses
  non-allow-listed targets (default empty allow-list → all refused; a listed
  target → allowed) and that **production-looking targets are refused** unless
  explicitly listed (and STAGING_ACCESS.md is the only doc that may name one).

### 9.2 Harnesses (parameterized, dry-runnable)

Extend `loadtests/` with a runner that takes `--target`, `--duration`,
`--rate`, `--dry-run`:
- `loadtests/harness_read_soak.py` — §12 read soak at 1× Growth RPS for a
  configurable duration (local: scaled-down RPS; record the scale factor).
- `loadtests/harness_marquee_spike.py` — ramp to a configurable peak RPS on one
  match (local: a few hundred RPS; the §12 50k figure is not run locally —
  state so).
- `loadtests/harness_deliveries_feed.py` — cursor pagination under load.
- `loadtests/harness_realtime_reconnect.py` — WS/SSE connect/reconnect churn
  (reuse the reference client in-process), measuring reconnect p95.
- `loadtests/harness_ingest_throughput.py` — ReplayAdapter replay at
  configurable speed multiplier; measures ingestion lag + DLQ growth.
- `loadtests/harness_correction_burst.py` — a burst of corrections (ReplayFaults
  correction-after-completion) while reads continue; asserts no lock contention
  errors and correct Tier-1 rebuild.
- `loadtests/soak.py` — the several-hour soak: read soak + replay ingest +
  realtime subscribers, **including a mid-soak deploy** (restart the app
  process) and a **replica failover** (see 9.4). Local minimum duration ≥ 2 h
  (a full multi-hour soak is a staging activity — UNVERIFIED; state the plan's
  target duration and what local evidence covers).

Each harness writes results to `reports/phase_i/load/<name>-<timestamp>.json`
with the §12 SLO targets and the measured p50/p95/p99/error-rate, tagged with
environment, instance sizes (local: `docker-compose` Postgres 17.11 + Redis +
single ASGI process — record exact versions/limits), the exact command, and
date. `--dry-run` validates inputs and prints the plan without running.

### 9.3 `docs/CAPACITY_MODEL.md` (absent → create)

Structure it as the capacity model with every figure tagged
`MEASURED|ASSUMED|UNVERIFIED` + environment/command/date. Map each §12 SLO to a
row: target, local measurement, status (`demonstrated` / `not demonstrated` /
`failed`), and **what a failure implies** (e.g. "read p95 > 200 ms locally →
the CDN/replica tiers in the plan are not optional; re-run on staging before
launch"). Explicitly state the local topology is smaller than the planning
stage and the extrapolation is an estimate (ASSUMED), not a guarantee.

### 9.4 Replica failover (attempt; fall back honestly)

- Attempt: extend `docker-compose` with a `db-replica` service
  (pg_basebackup bootstrap + `primary_conninfo` to the main Postgres) and the
  soak script promotes/demotes it mid-load, measuring read-continuity during
  failover. If this environment cannot run a streaming replica (WAL config,
  resources), record `UNVERIFIED` with the exact missing piece and do not fake
  the number.
- Deploy simulation: restart the ASGI app + run migrations forward/backward
  (the failed-migration scenario is Task 6) during the soak.

### 9.5 Tuning

Tuning is allowed only with evidence (before/after) recorded in the SDD ledger.
Do not tune to hide a gap; if a §12 target fails locally, record it as failed
with the implication.

### 9.6 Tests / verification

- `tests/phase_i/test_harness_safety.py` (9.1).
- Dry-run smoke of each harness in CI? No — harnesses are manual/long; CI runs
  only the safety tests. Document the run commands in `loadtests/README.md`.
- Manual run + `reports/phase_i/load/` evidence + `CAPACITY_MODEL.md` filled.
- Commit: `perf(phase-i): allow-listed load harnesses + capacity model + local results (Task 5)`.

---

## 10. Task 6 — Game day and operational readiness (commit 6)

### 10.1 Scripted game day (local topology, ReplayFaults)

`scripts/game_day/` (new; each scenario = one script that runs against the
local stack and records a report):
- `scenario_provider_outage.py` — ReplayFaults outage window during a live
  replay; measure detection (freshness alert condition) and recovery; verify
  breaker open → skip polls → auto half-open → resume from checkpoint.
- `scenario_schema_drift.py` — malformed/unknown-enum payload quarantined to
  DLQ; contract-test failure surfaced; measure detection → re-drive.
- `scenario_wrong_score.py` — a reported wrong score → reconcile_state →
  correction path → ETag change; measure time-to-correct.
- `scenario_replica_loss.py` — stop the replica (or mark UNVERIFIED if no
  replica — see 9.4), verify reads continue on primary.
- `scenario_redis_failover.py` — stop Redis; verify cache breaker fail-open +
  realtime degraded polling + recovery.
- `scenario_failed_migration.py` — apply a deliberately failing migration;
  verify the deploy halts and rollback restores (use a scratch DB, not the dev
  DB).
- `scenario_reconnect_storm.py` — after a deploy restart, a burst of clients
  reconnecting with `from_seq`; verify seq-based replay and no resync storm.
- `scenario_expired_credential.py` — the secret-manager abstraction returns an
  expired/invalid credential; verify the adapter surfaces a clean error, the
  breaker opens, and rotation without redeploy works (the
  `apps/live/services/credentials.py` path).

Each script writes `reports/game_day/<scenario>-<timestamp>.md`: detection
time, time-to-mitigate, runbook section used, `sufficient|gap` verdict, and any
runbook fix needed. **Runbook gaps are fixed in `docs/RUNBOOKS.md` in the same
commit** (the brief: "fix runbook gaps").

### 10.2 Alert routing E2E

- No STAGING_ACCESS on-call channel exists. Deliver: a local alert sink
  (e.g. `ops/alerts/local_sink.py` writing to `reports/game_day/alerts.log`)
  and a test that a raised alert condition routes to the sink and its
  `runbook` link resolves (existing contract test pattern). The real-channel
  step is documented as **blocked/UNVERIFIED** (needs STAGING_ACCESS.md).

### 10.3 Restore + derived-data rebuild drills (measured RTO/RPO)

- `scripts/game_day/drill_restore.py`: `pg_dump`/`pg_restore` (or PITR-style
  wal-g if available — else document) of the local DB into a scratch DB;
  measure **RTO** (restore wall time) and **RPO** (data loss window) from
  timestamps. Record in `reports/game_day/`.
- `scripts/game_day/drill_rebuild_derived.py`: `rebuild_statistics all` (and
  fantasy sweep if applicable) from canonical source; measure wall time and
  assert digest-validated rows reproduce (no double counting).
- Compare measured RTO/RPO against the §6 planning targets (RTO ≤ 4 h, RPO ≤
  15 min): state demonstrated/not-demonstrated; a local single-node restore
  will beat the target — say the local topology is smaller and the planning
  numbers remain UNVERIFIED at production scale.

### 10.4 Steward & support readiness — `docs/OPERATIONS.md` (absent → create)

Short operator guide: DLQ triage, identity queue, **match-merge queue**,
corrections, and the wrong-score process — each with the runbook link and the
expected queue volume **estimated from shadow data** (Task 2 report:
`shadow_*` counts → per-day DLQ/candidate/correction volumes; label the
estimate ASSUMED/UNVERIFIED). Add an `## Operator guide` pointer in
`docs/RUNBOOKS.md` and the README.

### 10.5 Tests / verification

- Scenario scripts are manual (long), but each gets a **dry-run smoke test** in
  `tests/phase_i/test_game_day.py` proving it validates its target via the
  allow-list and prints a plan without side effects.
- `make check` green; run the game-day scenarios against the local stack and
  commit `reports/game_day/*` evidence + runbook fixes.
- Commit: `ops(phase-i): game-day scenarios, alert sink, drills, OPERATIONS guide (Task 6)`.
---

## 11. Task 7 — Consumer integration validation (commit 7)

### 11.1 Proxy ladder (real proxy config, local)

No frontend team client or recorded requests are supplied (not in repo), so use
the Phase E reference client (`tools/reference_client/realtime_client.py`) per
the brief:
- `ops/proxy/nginx.conf` (new): a real proxy config in front of the app with
  the CDN/proxy behaviours that break naive SSE/WS: response buffering
  (`proxy_buffering`, `proxy_read_timeout`), gzip/compression for SSE, header
  handling (`X-Accel-Buffering: no` already sent by the app — verify the proxy
  honours it), and WS upgrade passthrough (`Upgrade`/`Connection`).
- `scripts/consumer_ladder.py`: runs the reference client across the fallback
  ladder — REST snapshot → SSE (`Last-Event-ID` resume) → WS (`from_seq`) →
  REST resync — through the proxy, measuring each hop and reporting
  **proxy-induced failures** (buffering delays, gzip on SSE breaking the event
  framing, timeout truncation, WS upgrade drops) to
  `reports/phase_i/consumer_ladder.md`.
- `tests/phase_i/test_consumer_ladder.py`: the ladder script's dry-run mode +
  unit tests for the proxy-config assertions (SSE `no-cache, no-transform`,
  `X-Accel-Buffering: no`, WS upgrade headers) without needing nginx running;
  the live proxy run is manual evidence.

### 11.2 API change-freeze + deprecation policy + `Sunset`

- **Gap found:** the API spec promises `Sunset` deprecation headers
  (CRICKET_API_SPECIFICATION.md line 27) but no header exists in code. Implement
  it (additive):
  - `apps/core/deprecation.py`: `DeprecationMeta` registry — a deprecated
    endpoint declares `sunset_at` (ISO date) + `replacement`; a small middleware
    or `renderer`/view-mixin appends `Sunset: <date>` + `Link: <replacement>;
    rel="successor-version"` to responses. No endpoints are deprecated in this
    phase — the mechanism ships with a test-only deprecated endpoint proving the
    headers, `410 Gone` behaviour for fully sunset endpoints, and additive-only
    compatibility (old fields never removed).
  - `docs/API_DEPRECATION_POLICY.md` (new): additive-only changes, minimum
    deprecation window (PROPOSED, e.g. 2 releases / 90 days), `Sunset` header
    semantics, and the change-freeze note for launch (no breaking API changes
    from the launch candidate onward).
- `tests/phase_i/test_deprecation.py`: header present, correct `Link`
  successor, additive-only golden-payload compat (existing golden payloads
  still validate against the OpenAPI schema — re-run the existing jsonschema
  contract tests).

### 11.3 Tests / verification

- `pytest tests/phase_i tests/api` green; `make check` green.
- Manual proxy run + `reports/phase_i/consumer_ladder.md`.
- Commit: `feat(api): Sunset deprecation headers + proxy ladder validation + deprecation policy (Task 7)`.

---

## 12. Task 8 — Security validation (commit 8)

### 12.1 CI security gates

- Run the existing gates (`make lint`, `typecheck`, `imports`, full `make
  check`) against the release candidate (the final Phase I commit). Record the
  pass in `docs/SECURITY_REVIEW.md`.
- Add a minimal secret scan to CI if absent: `make test-secrets` greps the tree
  for real-credential patterns (`.env` excluded) and fails on hits — small,
  deterministic, and aligned with the "no real credentials in the repo"
  constraint. (Check whether one already exists first; do not duplicate.)

### 12.2 DAST (staging only — local topology, allow-list-gated)

- `scripts/security/dast_scan.py`: wraps OWASP ZAP (if available in the
  environment; otherwise a documented equivalent scanner or a clearly-labelled
  NOT-RUN record — do not fake findings) against the local topology only,
  calling `assert_target_allowed` first (G3). Two passes: unauthenticated (the
  public surface) and authenticated (a steward/admin session on `/admin` and
  the ops surfaces).
- Findings tracked in `docs/SECURITY_REVIEW.md`: table of finding, severity,
  status (open/fixed/waived), remediation, owner. Fix verified findings in the
  same or next commit; **do not claim a penetration test occurred**.
- If an external pentest is planned, produce `docs/PENTEST_SCOPE.md` (scope,
  in/out, environment, contacts) + the remediation-tracking table template;
  mark it `planned, not run`.

### 12.3 Threat-model controls re-verified (SECURITY §12) with evidence

Re-verify against the real (local) deployment and record evidence in
`SECURITY_REVIEW.md`:
- `/metrics` not public: gated at URL registration (`ENABLE_METRICS_ENDPOINT`)
  — verify 404 when off, present when on (test exists; re-run + record).
- Webhook receiver authentication: `POST /ingest/live/push` HMAC + replay
  window, 404 when flag off, 503 without secret (tests exist — re-run).
- Realtime origin checks + connection caps (tests exist — re-run; record).
- **Admin path IP restriction: GAP** — no IP-restriction middleware exists for
  `/admin`. Record as an open finding with a proposed control (reverse-proxy
  allow-list at the edge; out of scope to build the edge here) and mark it
  UNVERIFIED for production. Do not claim it is handled.
- WAF rules: not present locally (no real CDN/WAF) — record as UNVERIFIED /
  depends on the deployment platform; the plan's §9 spike mitigations reference
  them as design, not evidence.

### 12.4 Tests / verification

- `tests/phase_i/test_security.py`: secret-scan gate runs clean; DAST script
  refuses non-allow-listed targets; the existing security-related tests
  (metrics gating, push HMAC, origin/caps) are wired into a single
  `make test-security` target (or reuse existing targets and document the
  mapping).
- `make check` + `make test-security` green; `docs/SECURITY_REVIEW.md` current.
- Commit: `security(phase-i): DAST findings, threat-model evidence, secret scan, SECURITY_REVIEW (Task 8)`.
---

## 13. Task 9 — Launch plan and go/no-go package (commit 9)

### 13.1 `docs/LAUNCH_PLAN.md`

Dark launch → canary/percentage rollout by traffic and by competition. Content:
- **Feature-flag sequence** (reference `docs/FEATURE_FLAGS.md`, created in this
  task — the full flag inventory with defaults and who flips what): e.g.
  `LIVE_INGESTION_ENABLED` → `PROVIDER_SHADOW_MODE` → push receiver →
  percentage traffic gate → per-competition rollout.
- **Stages:** dark launch (shadow-only ingest, no public exposure), canary
  (small % of traffic to one competition), staged percentage rollout (by
  traffic and by competition), full. Each stage: success metrics (from
  `LAUNCH_CRITERIA.md` + SLOs), **explicit abort criteria**, and the
  **rollback procedure** (flag off, cache purge, migration rollback where
  relevant).
- **Comms plan:** status page, incident roles (who pages whom), hypercare
  staffing + duration (PROPOSED, e.g. 2 operators × first marquee weekend).
- **First marquee-match playbook:** cache pre-warming, connection caps and
  expected fan-out, who watches what (dashboards, alert triage), the §12 spike
  mitigations.

### 13.2 `docs/GO_NO_GO.md`

For each launch criterion from `LAUNCH_CRITERIA.md`: status (`met` / `not met`
/ `unproven`), evidence link (`reports/*`, metrics), owner, residual risk.
Include:
- The open licensing items from `RISKS_AND_ASSUMPTIONS` (RSK-01/03/12 and the
  licensing checklist rows) with their current state (all open — gate G1 fails).
- Known limitations: coverage gaps, computed-vs-official statistics, rankings
  and commentary rights (commentary deferred, rankings provider-gated).
- **Be blunt:** list every unproven item (real provider behaviour, staging env
  numbers, production-scale SLOs, real on-call routing, WAF/admin-edge
  controls) rather than rounding up. The overall verdict will be **NO-GO for
  production launch** until the two gates are met — that is the honest
  outcome of this phase and must be stated.

### 13.3 `docs/FEATURE_FLAGS.md` (absent → create)

Inventory: every setting flag (Phase A–H + `PROVIDER_SHADOW_MODE` +
`MATCH_DEDUP_*`), default, effect, who flips it, and the launch-sequence
ordering referenced by `LAUNCH_PLAN.md`.

### 13.4 `RISKS_AND_ASSUMPTIONS.md` update

Close or re-rate every risk with evidence; convert assumptions to observed
facts where measured; add new risks found:
- RSK-01 (no licensed provider) — **open, unchanged** (gate G1); mitigation
  evidence added (ReplayAdapter boundary + shadow tooling now exist).
- RSK-02 (coverage gaps) — re-rated with shadow coverage-gap measurements
  (UNVERIFIED for real provider).
- RSK-03 (redistribution rights) — unchanged (gate G1); licensing policy
  mechanism verified.
- RSK-05/06/08/12 — re-rate with the evidence this phase produced (identity
  continuity, dedup, reconciliation, corrections) or mark unchanged with
  pointers.
- New risks: match-merge mistakes (mitigated by reversibility + audit),
  proxy-induced SSE/WS breakage (measured in Task 7), false confidence from
  local-only measurements (mitigation: the MEASURED/ASSUMED/UNVERIFIED tagging
  discipline).
- Assumptions → observed facts: adapter boundary contract-tested; identity
  link threshold exercised on real data; corrections path exercised via replay.

### 13.5 Post-launch section

In `GO_NO_GO.md` (or a `docs/POST_LAUNCH.md`): error-budget policy (e.g. 99.9%
→ 0.9% monthly error budget, burn-rate alerts — PROPOSED), SLO review cadence
(monthly), and the deferred backlog with triggers: commentary (licence signed),
multi-region (measured need), dedicated realtime gateway (fan-out threshold),
fantasy exposure (FANTASY_PRODUCT_DECISION.md present + product sign-off).

### 13.6 Roadmap + final report + coverage gate

- `CRICKET_IMPLEMENTATION_ROADMAP.md`: add a **Phase I — Pre-launch validation**
  section (DONE block) mirroring the Phase H pattern, with the two gate
  outcomes, key decisions, and effort deviation.
- `.coveragerc-phasei` + `make test-phasei` (high-risk modules: shadow
  compare/report, canonicalise, match merge, linking — ≥ 95% branch, app ≥ 90%
  line) wired into `make check`; add `reports/` and the new docs to the
  coverage/observability contract tests where they reference metrics/runbooks.
- `docs/PHASE_I_PRELAUNCH_REPORT.md` — the final report: (a) built+run, (b)
  gates satisfied, (c) measured vs targets per SLO, (d) shadow findings +
  identity/dedup outcomes, (e) decisions where docs were silent/conflicting,
  (f) go/no-go summary with every unproven item, (g) what could not be done and
  what it needs. This is the brief's deliverable; the reply to the user mirrors
  it.

### 13.7 Tests / verification

- Doc-integrity tests where cheap (`tests/phase_i/test_docs.py`): `LAUNCH_PLAN.md`,
  `GO_NO_GO.md`, `FEATURE_FLAGS.md` exist and `GO_NO_GO.md` contains the
  unproven-items section; flag inventory matches settings (spot-check a few
  names); `LAUNCH_CRITERIA.md` entries have owners.
- `make check` (with `test-phasei`) green; full suite counts recorded.
- Commit: `docs(phase-i): launch plan, go/no-go, risks update, roadmap, final report (Task 9)`.

---

## 14. Testing & verification summary (across tasks)

| Area | Tests |
|---|---|
| Adapter boundary | contract kit (unchanged), golden mapping, redaction logging, licensing degradation/retention dry-run (Task 1) |
| Shadow comparison | injected mismatches of every category; determinism; no auto-correction; report shape; flag gate; alert contract (Task 2) |
| Identity continuity | career-stats identical before/after mapping; no duplicate/split; DoB-mismatch cap; batch idempotence + dry-run (Task 3) |
| De-duplication | property tests (order independence, idempotence, no double count); timezone/date-line, rescheduled, double-header, super-over, abandoned; merge/split reversibility; selector exclusion; 301 (Task 4) |
| Harness safety | every load/chaos/DAST tool refuses non-allow-listed targets (Tasks 5/6/8) |
| Deprecation | `Sunset`/`Link` headers; additive-only golden-payload compat (Task 7) |
| Security | secret scan; DAST refusal; threat-model control re-verification (Task 8) |
| Regressions | Phases A–H all still green; no gates weakened; `make check` + `test-phasei` |

## 15. Acceptance criteria mapping (brief §Acceptance criteria)

1. Gate outcome stated prominently (Task 1); contract kit + golden tests run;
   licensing policy verified via dry-run evidence. *(Real adapter N/A — gate
   failed; stated.)*
2. Shadow mode produces dated report + metrics; `LAUNCH_CRITERIA.md` exists with
   owners (Task 2).
3. Player + match identity continuity with zero duplicate careers / no
   double-counted matches in the test sets; reversible match merge works
   (Tasks 3/4).
4. Staged load + soak results exist, **marked UNVERIFIED with the missing gate
   named**; capacity model separates MEASURED from ASSUMED (Task 5).
5. Game day run, runbooks corrected, RTO/RPO from measured drills (Task 6).
6. Security findings tracked; `SECURITY_REVIEW.md` current (Task 8).
7. `LAUNCH_PLAN.md` + `GO_NO_GO.md` complete, blunt, consistent with evidence;
   `RISKS_AND_ASSUMPTIONS` updated (Task 9).

## 16. Effort estimate + deviation tracking

Planning figure (dev-days, local-topology scope): **≈ 30** — T0/T1 2, T2 5,
T3 3, T4 7, T5 4, T6 4, T7 2, T8 2, T9 4 (incl. tests + review). The Phase H
report records the actual vs planned; this plan's estimate is a planning
figure, not a commitment. Expect upward pressure from the merge/split
reversibility work and the soak evidence; record the deviation in the SDD
ledger and the final report.

## 17. Conflicts resolved / decisions to record (SDD ledger)

Pre-seeded (verify/refine during execution):
- **D-I1** Both gates fail ⇒ no vendor adapter, no network; shadow/compare
  tooling against ReplayAdapter; all staged numbers UNVERIFIED. *(brief §Gates)*
- **D-I2** `PROVIDER_DECISION.md` / `STAGING_ACCESS.md` are created as
  gate-placeholders + templates (the brief says the gate *documents* must
  exist; a placeholder is the honest minimal form and `test_gate_docs.py` makes
  flipping it deliberate).
- **D-I3** Second provider namespace (`replay` DataProvider + namespaced ids)
  exercises `resolve()` across two providers on real data; the divergence
  limitation is documented (UNVERIFIED vs a real provider's view).
- **D-I4** Match dedup is primarily ingest-time canonicalisation (prevention)
  with a reversible post-hoc merge/split safety net; no data is ever deleted;
  `before/after` inventories make split exact and audited.
- **D-I5** Conflict policy: live provider authoritative for live + 24 h;
  historical source authoritative only when live provider lacks the match;
  conflicts always steward-flagged, never auto-corrected.
- **D-I6** `Sunset` header is implemented (the API spec promises it; the code
  lacks it) — additive, with a test-only deprecated endpoint.
- **D-I7** Admin IP restriction + WAF are documented gaps (UNVERIFIED), not
  claimed handled.
- Doc conflicts to record as found (DATABASE_SCHEMA wins on tables, ARCHITECTURE
  on boundaries): any place where a new table/column must be added to both the
  migration and the reference DDL, and any selector boundary change.

# Shadow mode

Continuous, non-public comparison of what the platform derives for a match
against the provider's own card and a second source, producing a dated report,
Prometheus metrics and a steward queue. **Shadow comparisons never auto-correct**
— a mismatch is flagged, never written back to the match rows
(SECURITY §9 flag-and-queue invariant).

> **Gate context (2026-11-13):** both Phase I gates fail. The shadow provider is
> the **ReplayAdapter** (`apps/providers/adapters/replay.py`); the "second
> source" is an independent derivation from the raw `delivery` table. Every
> measured value is `UNVERIFIED` against a licensed provider and against
> staging/production — see `docs/PROVIDER_DECISION.md` and
> `docs/STAGING_ACCESS.md`.

## How it works

Three independent card views are built per completed match
(`apps/shadow/cards.py`):

1. **Ours** — the canonical read models (`innings_summary`, `batting_entry`,
   `bowling_entry`, `match_result`) that public endpoints serve;
2. **Provider** — the adapter's own card (`get_scorecard`); replay today, a
   licensed adapter later;
3. **Second source** — an independent aggregation from raw `delivery` rows,
   mirroring the scoring engine's conventions (wides never a ball faced,
   no-balls count, bowler runs = wides + no-balls + runs off bat).

`apps/shadow/compare.py` diffs the three per category (`totals`, `wickets`,
`extras`, `result`, `batting`, `bowling`), with severity `critical` for
displayed-score categories and `diff` for line-level drift. A clean replay
corpus produces **zero** findings — that is the evidence the pipeline is
internally consistent.

## Running it

```sh
PROVIDER_SHADOW_MODE=true uv run python manage.py shadow_report [--day YYYY-MM-DD]
```

- The beat task `shadow.compare_all` runs the same pass when the flag is on.
- The flag is **off by default** (`config/settings/base.py`); the command and
  the task refuse to run when it is off (`tests/phase_i/test_shadow_gate.py`).
- Output: `reports/shadow/<YYYY-MM-DD>.md` + `.json` (directory override:
  `SHADOW_REPORT_DIR`).

## What it reports

- Mismatch rate by category (ours vs provider vs second source), severity.
- Ingestion-latency distribution (p50/p90/p99) from provider-event time to
  event-log time.
- Correction frequency; correction **lag** is not measurable in replay mode
  (no provider revision timestamp) and is reported as such.
- Coverage gaps, schema-drift events, unresolved-identity rate.
- The mandatory banner: `UNVERIFIED — environment gate not met`.

## Metrics and alerts

`shadow_*` metric family in `apps/core/metrics.py`:
`shadow_comparisons_total{category}`, `shadow_mismatches_total{category,severity}`,
`shadow_ingest_latency_seconds`, `shadow_correction_lag_seconds`,
`shadow_corrections_total`, `shadow_unresolved_identity_total`,
`shadow_schema_drift_total`, `shadow_coverage_gaps_total`.

Alerts in `infrastructure/ops/alerts/shadow_alerts.yml` (contract-tested against registered
metrics + `docs/RUNBOOKS.md` `## Shadow mode` section); thresholds are
PROPOSED (`docs/LAUNCH_CRITERIA.md`).

## Queueing findings

Every finding is handed to a reporter registered by `apps.ingestion`
(dependency inversion — `apps.shadow` never imports `apps.ingestion`, enforced
by the import-linter contract). The DLQ owner writes a redacted
`IngestionError` row (`stage="shadow_compare"`, class
`ShadowComparisonMismatch`) for steward adjudication.

## When a real provider lands

Flip `docs/PROVIDER_DECISION.md` (the provider gate), point
`SHADOW_COMPARISON_PROVIDER` at the licensed provider code, and re-run this
document's steps. The deployment must keep shadow-ingested data off the public
read path while the pass is validating; the shadow report/metrics remain the
evidence for `docs/LAUNCH_CRITERIA.md`.

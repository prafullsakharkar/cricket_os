# Launch Criteria — quantitative exit gates for go/no-go

Every criterion below has an **owner**, a **status**, and an **evidence
source**. Thresholds marked **PROPOSED** are engineering proposals for the
product owner to ratify or replace — they are deliberately not claimed as
decided. Status values: `proposed` (threshold not ratified), `met`, `not met`,
`unproven` (cannot be measured yet — the provider/environment gates fail).

> **Gate context (2026-11-13):** the provider gate (`docs/PROVIDER_DECISION.md`)
> and the environment gate (`docs/STAGING_ACCESS.md`) both **fail**. Shadow
> measurements are against the **ReplayAdapter** on the local topology only —
> every number is `UNVERIFIED` for a licensed provider and for staging/
> production. `GO_NO_GO.md` consumes this file: a criterion can only be `met`
> with ratified thresholds AND verified evidence from a real environment.

| # | Criterion | Proposed threshold | Owner | Status | Evidence source |
|---|---|---|---|---|---|
| LC-1 | Zero unreconciled result mismatches over the last N completed shadow matches | N = 50 (PROPOSED); 0 mismatches in the last 50 | Data steward | `unproven` (replay corpus only; N=50 not yet reached) | `reports/shadow/*.md`, `shadow_mismatches_total{category="result"}` |
| LC-2 | Ingestion lag p99 ≤ 60 s (the §12 SLO) | p99 ≤ 60 s | Platform eng | `unproven` (replay latency ≈ 0 — no network; real provider UNVERIFIED) | `shadow_ingest_latency_seconds` |
| LC-3 | Shadow DLQ not growing | Flat over a 7-day window (PROPOSED) | Ops | `unproven` | `IngestionError` count for `stage="shadow_compare"` |
| LC-4 | Mismatch rate by category below caps | totals/wickets/extras/result ≤ 0.1%; batting/bowling ≤ 0.5% (PROPOSED) | Data steward | `unproven` | `shadow_mismatches_total{category,severity}` / `shadow_comparisons_total{category}` |
| LC-5 | Coverage gaps below cap | ≤ 1% of completed matches in window (PROPOSED) | Ops | `unproven` | `shadow_coverage_gaps_total`, second-source window diff |
| LC-6 | Unresolved identity rate below cap | ≤ 5% of shadow-provider map rows unverified (PROPOSED) | Data steward | `unproven` | `shadow_unresolved_identity_total`, `manage.py link_provider_identities --dry-run` |
| LC-7 | Corrections applied within window | p99 correction lag ≤ 5 min (PROPOSED) | Platform eng | `unproven` (not measurable in replay mode — no provider revision timestamp) | `shadow_correction_lag_seconds` |
| LC-8 | Schema drift zero | 0 drift events over the last N passes (PROPOSED) | Platform eng | `unproven` | `shadow_schema_drift_total`, adapter contract kit |

## How to promote a criterion

1. The owner ratifies/replaces the PROPOSED threshold (this file is updated,
   status leaves `proposed`).
2. The evidence source must be **measured against a real environment**
   (staging per `docs/STAGING_ACCESS.md`) or a **licensed provider**
   (`docs/PROVIDER_DECISION.md`), not the replay/local-topology stand-ins.
3. `GO_NO_GO.md` re-evaluates every criterion with the updated status.

Nothing in this file may be marked `met` while the two gates fail; the
placeholder markers in `tests/test_gate_docs.py` enforce the gate documents'
honesty.

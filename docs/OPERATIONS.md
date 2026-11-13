# OPERATIONS.md — steward & support readiness

Short operator guide for the queues and processes a steward handles day to day.
Each item links its runbook in `docs/RUNBOOKS.md` and states the expected queue
volume. **Queue volumes are `ASSUMED`/`UNVERIFIED`** — the only measured data is
the local replay corpus (`tests/fixtures/provider_live/`), which is not a real
provider's view (provider gate `docs/PROVIDER_DECISION.md` not met). Production
volumes must be re-estimated from staging shadow data before launch.

## The queues at a glance

| Queue | Table / signal | What lands here | Runbook |
|---|---|---|---|
| DLQ | `ingestion_error` (open rows) | validation/contract/adapter/reconciliation failures, redacted payloads | [DLQ triage](RUNBOOKS.md#dlq-triage) |
| Identity queue | `provider_entity_map` non-primary rows + unresolved candidates | player/team/venue identities the auto-linker could not resolve | [Identity conflict](RUNBOOKS.md#identity-conflict) |
| Match-merge queue | `match_duplicate_candidate` rows | same-match fixtures from two providers scoring in the review band (0.60–0.95) | [Match merge](#match-merge-queue) |
| Corrections | `DataCorrection` rows (via `apply_delivery_correction`) | provider-pushed revisions of delivery content | [Wrong score complaint](RUNBOOKS.md#wrong-score-complaint) |

## 1. DLQ triage

Every `ingestion_error` row is a redacted record of a failure the pipeline
could not absorb (DATA_INGESTION §4). Triage order:

1. **Read the row** — `stage` (`validate` / `contract` / `adapter` /
   `reconciliation_mismatch` / …), `error_class`, `error_detail`, and the
   `payload` (sensitive keys are already `[REDACTED]`).
2. **Classify** — schema drift → [Schema drift](RUNBOOKS.md#schema-drift);
   upstream outage → [Provider outage](RUNBOOKS.md#provider-outage);
   reconciliation mismatch → [Wrong score complaint](RUNBOOKS.md#wrong-score-complaint).
3. **Re-drive** — fix the cause and re-run the import/`sync_fixtures` for the
   affected scope; the DLQ row is marked `resolved` only after the re-drive
   succeeds (never edit match rows from the queue).

**Expected volume.** `ASSUMED` — a clean replay corpus produces zero DLQ rows
(`reports/phase_i/task1_licensing_evidence.md`-era runs). At a few matches/day
of live ingestion, malformed/contract rows should be ~0/day and
reconciliation-mismatch rows rare (only when a provider revision disagrees with
the engine). `UNVERIFIED` against a real provider.

## 2. Identity queue

Unresolved identities land in the identity queue via
`manage.py link_provider_identities` (Phase 0). Review candidates, confirm or
reject, and re-run. Auto-linking is deterministic and idempotent.

**Expected volume.** `ASSUMED` — the local replay corpus linked 3/3 players
with 0 pending candidates and 0 stubs (`reports/phase_i/identity_coverage.md`).
At a real provider's scale expect a small daily candidate backlog for newly
seen players; `UNVERIFIED` until the provider gate is met.

## 3. Match-merge queue

`match_duplicate_candidate` rows hold same-match pairs from two providers that
scored in the review band (0.60 ≤ score < 0.95) or that failed the auto
threshold. Steward actions (admin, reversible): `confirm_match_merge` (merges
the pair, reversible via `split_match`), `reject_match_candidate`, and
`split_match`. Merging never deletes data (docs/MATCH_DEDUPLICATION.md §4).

**Expected volume.** `ASSUMED` — zero candidates on the current corpus (a
single replay provider). When a second provider is added, expect a burst of
review-band pairs for overlapping windows, then a low steady-state;
`UNVERIFIED` until real dual-provider data exists.

## 4. Corrections

A provider revision of delivery content is applied through the correction
service (`apply_delivery_correction`) — idempotent, per-match advisory lock,
re-derives the innings and invalidates `match:{id}` (ETag changes). The
wrong-score process is [Wrong score complaint](RUNBOOKS.md#wrong-score-complaint):
`reconcile_state` opens a job and DLQ row on disagreement; a confirmed
correction only happens when the provider pushes a revision.

**Expected volume.** `ASSUMED` — rare on the replay corpus (corrections are
fault-injected, not observed organically). At a real provider expect a handful
per week for completed matches and near-zero during live play; `UNVERIFIED`.

## 5. Wrong-score process

1. Complaint arrives (user report or `ShadowVerifyMismatch` alert).
2. `reconcile_state` compares our engine figures against the provider card —
   a disagreement opens a `ReconciliationJob` + `reconciliation_mismatch` DLQ
   row (no silent correction).
3. If the provider pushes a revision, `apply_delivery_correction` fixes the
   score; the match version bumps so caches/ETags change.
4. Close the loop in the runbook [Wrong score complaint](RUNBOOKS.md#wrong-score-complaint).

## 6. Game-day drills & evidence

The game-day scenario scripts (`scripts/game_day/`) and their reports
(`reports/game_day/`) exercise every queue above against the local stack.
Drill status is recorded honestly: replica-loss and Redis-failover drills are
`UNVERIFIED` in this environment (missing pieces named, never faked).

## Readiness status

- **Sinks & runbooks**: alert routing is delivered to a local sink
  (`ops/alerts/local_sink.py` → `reports/game_day/alerts.log`); the real
  on-call channel step is **blocked/UNVERIFIED** (needs `docs/STAGING_ACCESS.md`).
- **Queue volumes**: `ASSUMED` from the replay corpus; re-estimate from staging
  shadow data before launch.

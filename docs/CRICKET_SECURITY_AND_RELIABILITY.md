# CRICKET_SECURITY_AND_RELIABILITY.md

Security, reliability, and data-quality controls for Cricket OS. This document
satisfies Phase 10 of the brief and is referenced from the README.

> **Separation of concerns:** *operational access control* (who may read/write
> what inside the platform) is distinct from *licensing / content-distribution
> rights* (what data we are legally permitted to display or redistribute). Both
> are enforced, in different layers.

---

## 1. Authentication & authorization

- **Public surface is read-only.** All score/statistics endpoints are public and
  unauthenticated; no public writes exist (per the brief).
- **Admin/ops surface** (`/admin`, correction, ingestion, reconciliation)
  requires **OAuth2/JWT** with **RBAC** roles:
  - `viewer` — read admin dashboards.
  - `operator` — run ingestion/reconciliation jobs.
  - `data_steward` — approve corrections and identity merges.
  - `admin` — manage providers, rulesets, users.
- **Least privilege** at every layer: DB roles per service (`web`, `worker`,
  `readonly_replica`, `ingest`), each with only the required grants; providers
  get a write-only credential to their own tables/flow.
- **MFA** required for `admin`/`data_steward`.

## 2. API abuse prevention

- Token-bucket rate limits per IP and per API key; stricter buckets on
  ball-by-ball/commentary endpoints.
- WAF/edge protection in front of the app; bot management for public endpoints.
- Request-size and parameter allow-listing; cursor validation; pagination caps.
- No verbose errors that leak internals; generic `5xx` to clients, detailed logs
  server-side.

## 3. Provider credential management

- Secrets live in a **secret manager** (AWS Secrets Manager / Vault); the DB
  stores only `data_provider.credential_ref`, never the secret.
- Credentials are injected at runtime into the adapter; never logged, never in
  error payloads, never in `IngestionError.payload` (redaction enforced).
- Rotation supported without redeploy (adapter reads the current secret per run).

## 4. Audit logging & provenance

- **`DataCorrection`** and **`FantasyPointsLedger`** are append-only.
- A global **`AuditLog`** records admin actions (who, what, before/after,
  timestamp, IP).
- **Provenance** (`source_provider_id`, `provider_updated_at`, `ingested_at`,
  `revision`, `ProviderEntityMap`) lets any figure be traced to its origin and
  version — essential for disputes and for distinguishing official vs. derived
  values.

## 5. Schema & input validation

- All provider payloads validated against **strict schemas** (types, enums,
  ranges) before persistence; unknown enum values are **quarantined** to the DLQ,
  not coerced.
- Domain `CHECK` constraints (runs ≥ 0, wicket requires dismissal type, PK/FK)
  as a second line of defence.
- API inputs validated by DRF serializers (query params, path UUIDs, allow-lists).

## 6. Backups & disaster recovery

| Control | Decision |
|---|---|
| Backups | Managed PostgreSQL PITR + daily snapshots; retain ≥ 30 days |
| Cross-region | Encrypted backups replicated to a second region |
| Redis | Treated as **cache/ephemeral**; rebuildable from DB, not backed up as truth |
| RTO / RPO | RTO ≤ 4 h, RPO ≤ 15 min (planning target) |
| Restore drills | Quarterly restore from backup into a staging environment |
| Secrets | Backed up in the secret manager with recovery procedure |

## 7. Migration rollback strategy

- **Expand/contract** migrations are individually reversible; each migration has
  a tested down-path for the *current* release (data-destructive contract steps
  are gated behind a feature flag + backup).
- Deploys run migrations before code switch-over; a failed migration blocks the
  deploy. A rollback plan and a "migration halt" runbook exist.
- Never drop a column in the same release that stops writing to it.

## 8. Monitoring & alerting

- **Infrastructure:** CPU/mem/conn-pool saturation, Redis hit rate, replica lag.
- **Application:** request latency/error SLIs, 5xx rate, throttle rate.
- **Ingestion:** provider lag, DLQ depth, correction rate, circuit-breaker state.
- **Data freshness:** per-live-match `freshness_seconds`; alert > 60 s.
- **Alerts** route to on-call with runbooks; every alert maps to an actionable
  runbook (see §10).

## 9. Data quality & correctness controls

| Risk | Control |
|---|---|
| **Duplicate players** | `provider_entity_map` + `player.name_key` (name+DoB) scored matching; steward review before merge; merge creates a redirect map |
| **Duplicate matches** | `ux_match_provider` + `(series, teams, start_time)` heuristic for cross-provider |
| **Incorrect score** | Rebuild always derives scores from stored deliveries; reconciliation diff against provider final card; corrections leave an audit trail |
| **Missing balls** | Reconciliation backfills sequence gaps by re-fetching the innings |
| **Out-of-order** | `delivery_seq` ordering + optimistic accept + rebuild (never order-dependent) |
| **Provider schema drift** | Contract tests + validation quarantine + alert |
| **Stale data** | Freshness monitor + `meta.stale` on responses |
| **Partial historical data** | `innings_summary.batch_complete=false`; derived metrics marked estimated |

## 10. Ingestion failure recovery

- Idempotent replay from the last committed `SyncCheckpoint`.
- DLQ triage UI: inspect, fix mapping, re-drive (re-enqueue with the same
  idempotency key → safe).
- Runbooks: "provider outage", "schema drift", "stuck checkpoint",
  "wrong score complaint", "identity conflict".
- Every recovery path must be safe to run **twice**.

## 11. Licensing vs. access control

- A `LicensedContentPolicy` per provider records: display rights, redistribution
  rights, retention limits, attribution requirements.
- Endpoints check the policy and either serve, omit, or return
  `403 license_required` for commentary/derived feeds.
- Attribution is rendered wherever licences require it.
- Retention limits (e.g. "delete ball-by-ball after N days") are enforced by a
  scheduled job.
- **Attribution and licence notes are configuration, not code** — so a licence
  change does not require a redesign.

## 12. Threat model summary

| Threat | Mitigation |
|---|---|
| Credential theft | Secret manager, no secrets in DB/logs, rotation |
| Data scraping / abuse | Rate limits, WAF, CDN, bot management |
| Tampering with scores | Read-only public API; audited admin corrections; derived-from-source scoring |
| Provider outage cascade | Circuit breakers, cache fallback, `stale` marking |
| Insider misuse | RBAC + append-only audit + least privilege |
| DoS on marquee match | CDN absorption, coalesced realtime, throttling |

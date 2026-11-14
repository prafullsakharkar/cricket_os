# Feature flags

Inventory of every environment flag in the platform, with its default, effect,
who flips it, and its place in the launch sequence (see `LAUNCH_PLAN.md` §2).
"Who flips it" follows the operator roles in `docs/RUNBOOKS.md`; everything
below is a **config change, never a code change** (all read via
`django-environ` in `config/settings/base.py`).

Status values: `safe` (flipping needs no deploy and no data migration),
`deploy-coupled` (must be set at deploy time), `gate` (a launch gate in
`LAUNCH_PLAN.md`).

## Launch-sequence flags (the rollout order)

| Flag | Default | Effect | Who flips | Status | Launch step |
|---|---|---|---|---|---|
| `LIVE_INGESTION_ENABLED` | off | Master switch for live match polling/ingestion. Off ⇒ the live path is inert (no polling, no delivery append). | Platform eng | `safe`, `gate` | Step 1 — dark launch turns it ON for shadow-only ingestion |
| `PROVIDER_SHADOW_MODE` | off | Runs the shadow comparison pass (beat task + `shadow_report`) against `SHADOW_COMPARISON_PROVIDER`. While the provider gate (G1) is unmet the shadow provider is the internal **ReplayAdapter** and every measured value is UNVERIFIED. | Platform eng | `safe`, `gate` | Step 1 (dark), Step 3 (live shadow) |
| `SHADOW_COMPARISON_PROVIDER` | replay | Provider code the shadow pass compares against. Flips to the licensed provider code the moment G1 is met. | Platform eng | `safe` | Step 3 |
| `LIVE_PUSH_RECEIVER_ENABLED` | off | Registers/activates `POST /ingest/live/push`. Off ⇒ the route 404s. | Platform eng | `safe`, `gate` | Step 4 — push receiver cutover |
| `LIVE_PUSH_HMAC_SECRET` | — | HMAC key for the push receiver (from the secret manager; never in the repo). Empty ⇒ the receiver answers 503. | Platform eng + Security | `deploy-coupled` | Step 4 |
| `ENABLE_METRICS_ENDPOINT` | off | Registers `/metrics` (Prometheus). Off ⇒ 404. | Platform eng | `safe` | Step 2 (observability on before traffic) |

## Data-quality gates

| Flag | Default | Effect | Who flips | Status |
|---|---|---|---|---|
| `LIVE_SHADOW_VERIFY_EVERY` | — | Verify every Nth live shadow comparison (bounded sampling). | Data steward | `safe` |
| `MATCH_DEDUP_START_TOLERANCE_MINUTES` | 180 | Same-day start-time tolerance for candidate duplicate matches. | Data steward | `safe` (tuned, not a gate) |
| `MATCH_DEDUP_AUTO_THRESHOLD` | 0.95 | Similarity ≥ this auto-merges a duplicate pair. | Data steward | `safe` |
| `MATCH_DEDUP_REVIEW_THRESHOLD` | 0.60 | Similarity ≥ this but < auto ⇒ review-band candidate. | Data steward | `safe` |

## Realtime / abuse-control tuning

| Flag | Default | Effect | Who flips | Status |
|---|---|---|---|---|
| `REALTIME_SSE_HEARTBEAT_SECONDS` | 15 | SSE keep-alive interval. | Platform eng | `safe` |
| `REALTIME_ALLOWED_ORIGINS` | empty | Origin allow-list for WS/SSE (empty ⇒ same-origin only). | Platform eng | `safe` |
| `REALTIME_CONNECTION_CAP_PER_IP` / `_PER_MATCH` / `_PER_NODE` | configured | Connection caps for the realtime transports. | Platform eng | `safe` |
| `RATELIMIT_BACKEND` / `RATELIMIT_KEY_HEADER` | configured | Throttle store + client-key header. | Platform eng | `safe` |
| `CACHE_SURROGATE_HEADER` / `CACHE_PURGE_URL` | configured | CDN surrogate-key header + purge endpoint. | Platform eng | `deploy-coupled` (must match the CDN) |

## Fantasy (Phase H — product/legal gated)

| Flag | Default | Effect | Who flips | Status |
|---|---|---|---|---|
| `FANTASY_PUBLIC_API_ENABLED` | off | Registers the public fantasy read routes. Off ⇒ 404. Stays OFF until `FANTASY_PRODUCT_DECISION.md` exists and product+legal sign off. | Product owner | `safe`, `gate` (fantasy only) |
| `FANTASY_PROVISIONAL_ENABLED` | off | Computes provisional (live-ish) points labelled provisional. | Product owner | `safe` |
| `FANTASY_DEFAULT_AUTO_RECOMPUTE` | true | Auto-recompute final points when a ruleset freezes. | Product owner | `safe` |
| `FANTASY_ENGINE_VERSION` | — | Version stamped on every calc-audit row. | Platform eng | `deploy-coupled` |

## Environment / safety

| Flag | Default | Effect | Who flips | Status |
|---|---|---|---|---|
| `DJANGO_ENVIRONMENT` | — | Environment label (`dev`/`test`/`prod`). | Platform eng | `deploy-coupled` |
| `DJANGO_DEBUG` | — | Debug mode; must be off in prod (enforced by `config/settings/prod.py`). | Platform eng | `deploy-coupled` |
| `DJANGO_SECRET_KEY` | dev default | Django secret; prod refuses to start without a real one. | Platform eng + Security | `deploy-coupled` |
| `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` | — | Host/origin allow-lists for prod. | Platform eng | `deploy-coupled` |
| `ENABLE_API_SCHEMA` | off | Registers `/api/v1/schema` (dev/test only; **never in prod**). | Platform eng | `safe` |
| `TARGET_ALLOWLIST` | empty | G3 harness allow-list (`host:port`). Empty ⇒ every load/chaos/DAST harness refuses to run. `docs/STAGING_ACCESS.md` is the only document allowed to name a real environment's target. | Platform eng | `safe` |
| `LIVE_*` breaker/poll tuning | configured | Outage-budget thresholds, poll cadence, event retention, reconciliation window. | Platform eng | `safe` |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` / `CELERY_TASK_ALWAYS_EAGER` | — | Queue wiring. | Platform eng | `deploy-coupled` |
| `REDIS_URL` / `CACHE_REDIS_URL` / `PUBSUB_REDIS_URL` / `RATELIMIT_REDIS_URL` | — | Redis endpoints (cache/pubsub/ratelimit). | Platform eng | `deploy-coupled` |

## Rules of thumb

- **A flag is flipped in one direction per environment.** Dark launch flips
  ingestion on; canary flips it on for a percentage; full flips it on for all.
  Rollback = flip the flag off (no deploy) + cache purge where the flag gates
  a route.
- **No flag gates a route that must be public.** Public read endpoints are
  always registered; only ops surfaces and the live ingest path are gated.
- **Secrets are never flags.** `LIVE_PUSH_HMAC_SECRET`, `DJANGO_SECRET_KEY`
  and provider credentials live in the secret manager and are injected as env
  at deploy time; `make test-secrets` fails CI if a real credential is ever
  committed.

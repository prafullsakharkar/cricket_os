# Launch plan

Dark launch → canary → staged percentage rollout (by traffic and by
competition) → full. Every stage has success metrics (from
`LAUNCH_CRITERIA.md` + the §12 SLOs), an **explicit abort criteria**, and a
**rollback procedure**. The feature-flag sequence references
`docs/FEATURE_FLAGS.md`.

**Gate context (2026-11-13):** gates G1 (provider) and G2 (staging) both fail.
This plan is the **runbook for the first launch after both gates are met** —
it cannot start today. Everything measured in Phase I is local-only and
UNVERIFIED; nothing here claims a launch has happened.

---

## 1. Launch gates (must be met before step 1)

1. **G1 provider gate** — `docs/PROVIDER_DECISION.md` present with a licensed
   provider decision; the adapter boundary contract-tested against the real
   provider feed.
2. **G2 environment gate** — `docs/STAGING_ACCESS.md` present naming the
   staging target; the `TARGET_ALLOWLIST` names only that target; a staging
   soak + failover drill has run (the Task 5/6 drills re-run on staging are
   the evidence).
3. **LC-1…LC-8** re-evaluated on staging evidence (`GO_NO_GO.md` re-run); a
   criterion may be `met` only with ratified thresholds AND staging/real
   provider evidence.
4. **`make check` green** on the release candidate; `make test-secrets` clean;
   `SECURITY_REVIEW.md` current; no open high/medium exploitable finding.

## 2. Feature-flag sequence (who flips what, when)

| Step | Flags flipped | Effect |
|---|---|---|
| 0 (prep) | `DJANGO_ENVIRONMENT=prod`, `DJANGO_DEBUG=False`, real `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`/CSRF origins, `ENABLE_API_SCHEMA` **off**, `ENABLE_METRICS_ENDPOINT` on, `CELERY_*`/`REDIS_*` wired, `TARGET_ALLOWLIST` = staging only | Production configuration is hardened (`check --deploy` clean) |
| 1 (dark) | `LIVE_INGESTION_ENABLED=on` + `PROVIDER_SHADOW_MODE=on` (`SHADOW_COMPARISON_PROVIDER` = licensed provider once G1 met) | Provider ingestion runs **shadow-only**: no public exposure; every payload compared; mismatches → DLQ + metrics |
| 2 (observe) | no new flags; `shadow_report` daily, alert sink live | Shadow data-quality evidence accumulates (LC-1…LC-8) |
| 3 (live shadow) | `LIVE_PUSH_RECEIVER_ENABLED=on` + `LIVE_PUSH_HMAC_SECRET` from the secret manager | Push receiver accepts the provider's live pushes (HMAC + replay window); still no public exposure |
| 4 (canary) | percentage traffic gate (see §3) — **no new flag**: canary = a % of traffic served from the new data | A small % of real users read provider-backed data |
| 5 (staged %) | raise the percentage gate by traffic and by competition | Rollout expands competition-by-competition |
| 6 (full) | percentage gate = 100% | Full launch; dark/shadow flags may be left on for monitoring |

Flags are flipped **one direction per environment**; rollback is always "flip
it off" (see §4). Fantasy stays OFF (`FANTASY_PUBLIC_API_ENABLED=false`) until
the fantasy product/legal gate (`FANTASY_PRODUCT_DECISION.md`) exists.

## 3. Stages

### Stage A — Dark launch (shadow-only ingest)

- **What:** provider ingestion + shadow comparison run continuously; nothing
  is served to users from the new data. This is exactly the Phase I shadow
  mode, but against the real provider feed (G1) and on staging (G2).
- **Success metrics:** LC-2 ingestion lag p99 ≤ 60 s; LC-3 shadow DLQ flat
  over a 7-day window; LC-4 mismatch rate by category below caps; LC-5
  coverage gaps ≤ 1%; LC-6 unresolved identity ≤ 5%; LC-7 corrections p99 ≤
  5 min; LC-8 schema drift zero over the last N passes.
- **Abort criteria (any ⇒ dark launch stops, provider feed quarantined):**
  - LC-4 mismatch rate > cap for two consecutive days (data-quality fault);
  - LC-3 DLQ growing for 24 h (ingestion pipeline fault);
  - LC-8 schema-drift events not quarantined within the runbook window;
  - any 5xx in the ingest path for 15 consecutive minutes.
- **Rollback:** `LIVE_INGESTION_ENABLED=off` + `PROVIDER_SHADOW_MODE=off`
  (config only, no deploy). No cache purge needed (nothing was served).

### Stage B — Canary (small % of traffic, one competition)

- **What:** a small percentage of reads (PROPOSED start: 1%) for ONE
  competition are served from provider-backed data; the rest stay on the
  current baseline. The percentage is a **traffic gate at the edge/CDN**
  (weighted routing), not a code flag.
- **Success metrics (SLOs):** read p95 ≤ 200 ms (cached); error rate < 0.1%;
  realtime event latency ≤ 1 s; freshness lag ≤ 60 s; no `stale`-marked reads
  beyond the alert threshold.
- **Abort criteria (any ⇒ revert the canary to 0%):**
  - error rate ≥ 0.1% for 10 min;
  - read p95 ≥ 200 ms for 30 min (after the CDN/cache tier is confirmed);
  - any provider-sourced correction that produces a wrong score visible to
    users (reconciliation mismatch escapes);
  - a 5xx spike attributable to the new data path.
- **Rollback:** traffic gate → 0% (edge config, minutes). Cache purge for the
  canary competition's resources. The baseline data is untouched.

### Stage C — Staged percentage rollout (by traffic and competition)

- **What:** increase the traffic gate in steps (PROPOSED: 5% → 25% → 50% →
  75%) and add competitions one at a time (low-risk domestic first, marquee
  last). Each step holds for ≥ 1 day (PROPOSED) before the next.
- **Success metrics:** same SLOs as Stage B, plus per-competition coverage and
  mismatch checks; no cross-competition data bleed.
- **Abort criteria (any ⇒ hold or revert to the previous step):**
  - a competition's LC-4/LC-5 numbers degrade;
  - provider outage during the step (the RSK-06 drill behaviour must hold:
    breaker opens, `stale` served, no 5xx);
  - reconciliation backlog grows beyond the runbook threshold.
- **Rollback:** traffic gate to the last healthy step (edge config); the
  outage runbook (`docs/RUNBOOKS.md#provider-outage`) governs the provider
  failure case.

### Stage D — Full

- **What:** 100% traffic on provider-backed data. Shadow comparison stays on
  for monitoring (shadow mode is harmless and valuable).
- **Success metrics:** the full SLO set sustained over the first marquee
  weekend; error budget not burned at an alarming rate (§6).
- **Abort criteria:** same as Stage C at the 100% step.
- **Rollback:** as Stage C.

## 4. Rollback procedures (summary)

| Trigger | Action | Time |
|---|---|---|
| Data-quality fault (mismatches, DLQ growth, schema drift) | `LIVE_INGESTION_ENABLED=off`; provider feed quarantined to the DLQ; `PROVIDER_SHADOW_MODE` stays on to keep comparing | minutes |
| Canary/staged traffic fault (error rate, p95, wrong scores) | traffic gate → previous healthy % (or 0% in Stage B); cache purge of affected resources | minutes (edge config) |
| Provider outage | Run `docs/RUNBOOKS.md#provider-outage`: breaker opens, last-known-good + `stale` served, polling resumes on recovery | automatic (breaker) |
| Wrong-score / correction escape | Run `docs/RUNBOOKS.md#wrong-score`: reconcile, DLQ the bad card, correction bumps the version/ETag, purge caches | per runbook |
| Migration fault | `docs/MIGRATION_WORKFLOW.md`: reverse the migration (all migrations are reversible — verified by the failed-migration game-day drill) | per runbook |

## 5. Comms plan

- **Status page** — public status at the CDN edge (PROPOSED: `status.example.com`);
  degraded/full-outage states wired to the alert sink
  (`infrastructure/ops/alerts/local_sink.py` → on-call in staging).
- **Incident roles** — `docs/RUNBOOKS.md` §Incident roles: a primary on-call
  (pages), a secondary (escalation), and a data steward on-call for
  data-quality pages (DLQ growth, mismatch caps). Who pages whom is the
  runbook table; the alert sink's runbook links are validated to resolve.
- **Hypercare** — PROPOSED: **2 operators × the first marquee weekend** (from
  the first Stage-C step through the first full weekend), plus the data
  steward during live matches. Hypercare watches: dashboards (§6), the alert
  queue, DLQ depth, and the reconciliation job.

## 6. First marquee-match playbook

- **Pre-match (T-2h):**
  - **Cache pre-warming** — warm the live match, scorecard, and squads
    resources (the app builds-on-miss; pre-warm avoids a cold thundering
    herd at T-0). Purge any stale surrogate keys.
  - **Connection caps** — confirm `REALTIME_CONNECTION_CAP_PER_IP/_MATCH/_NODE`
    and the throttle buckets (default 10/s, 120 burst) match the expected
    fan-out model for the match (the load harnesses measured 0 5xx while the
    origin shed 40% of the spike as 429s — the CDN must absorb, the app
    sheds).
  - **Expected fan-out** — from `docs/CAPACITY_MODEL.md`: at 1× Growth read
    RPS ≈1,500 with p95 16.6 ms locally (0.01× scale); the marquee 50k RPS is
    a CDN-absorption figure, re-verified on staging before the match.
  - **Who watches what:** operator 1 — read SLO dashboards (p95/error) +
    realtime fan-out; operator 2 — ingest lag + DLQ + reconciliation + alert
    queue; data steward — shadow comparison + mismatch caps.
- **Live match:**
  - Watch `INGEST_LATENCY`, `MATCH_FRESHNESS_SECONDS` (alert if > 60 s),
    breaker state, cache hit ratio (if the CDN exposes it), and the
    `stale`-served counter.
  - Any breach of a §12 SLO for 15 min triggers the abort/rollback of the
    relevant stage (§3–4).
- **Post-match:** reconcile the final result (LC-1 window), run the wrong-score
  review, publish the post-match SLO summary, and review the marquee
  mitigations (§9 spikes) against what was observed.

## 7. §12 spike mitigations (design → verified at launch)

| Mitigation | Status | Launch verification |
|---|---|---|
| CDN absorption + surrogate-key purge | design (no local CDN) | verify cache-hit ratio and 429 shedding on staging at the marquee figure |
| Coalesced realtime (one hub fan-out) | verified locally | re-verify at staging fan-out |
| Rate limits (per-IP + per-key) | verified locally | confirm scope tuning per competition |
| Circuit breakers + `stale` serving | verified locally (drills) | re-run the provider-outage + Redis-failover drills on staging |
| Read replicas | **UNVERIFIED** (no replica in the local topology) | staging drill (the replica-loss game-day scenario recorded UNVERIFIED with the missing pieces named) |
| WAF / bot management | **UNVERIFIED** (platform-dependent, F-07) | platform configuration + `docs/SECURITY_REVIEW.md` re-review |

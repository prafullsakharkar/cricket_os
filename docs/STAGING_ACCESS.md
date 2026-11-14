# STAGING_ACCESS.md — production-like staging environment access

> ## Gate NOT met (as of 2026-11-12)
>
> **There is no named production-like staging environment.** This document is a
> **placeholder and template**: it states the Phase I environment gate outcome
> and defines what a real staging declaration must contain. Filling it in IS
> the gate — `tests/test_gate_docs.py` asserts the "Gate NOT met" marker until
> a real environment is declared and deliberately removes it.
>
> Consequences while the gate is unmet (Phase I brief §Gates):
> - Load, chaos, restore/rebuild and DAST work is delivered as
>   **parameterized, dry-runnable harnesses** and run only against the
>   **local production-topology stack** (Postgres + Redis + ASGI app — see
>   `.env`).
> - **Every staged result is marked `UNVERIFIED`** and names this gate as the
>   missing precondition.
> - The safety rule (Phase I brief §Gates G3) still holds: **no harness runs
>   against any target not in its allow-list** (`TARGET_ALLOWLIST`), and
>   production is never a target.

---

## What a real staging declaration must contain (template)

Fill every section from the actual staging environment. A declaration with
blanks is not a declaration.

### 1. Environment identity

- Environment name (e.g. `staging-us-east-1`), URL(s), and how it differs from
  production (instance sizes, replica count, CDN on/off, feature flags).
- **Topology:** app nodes, Postgres primary/replicas, Redis roles, proxy/CDN,
  PgBouncer, secret manager. Instance sizes per component.
- Data state: how it is kept current (restore from prod snapshot? replay?).

### 2. Endpoints and limits

- Public API base URL; `/metrics` availability; admin URL.
- Rate limits configured on staging; request ceilings; any bot/WAF settings.
- Load-test budgets if the environment imposes any (e.g. "do not exceed
  N RPS"; "soak only 08:00–18:00 UTC").

### 3. Credentials at runtime

- How credentials are supplied at runtime (secret manager refs, not files).
- How the load/chaos/DAST harnesses authenticate (which roles, which secrets).
- Rotation procedure and expiry policy.

### 4. Who to notify

- On-call contact/channel for incident alerts; owner of the staging env;
  approval path for a soak/chaos run (who signs off, how long in advance).
- Whether real on-call alert routing can be exercised on staging (the Phase I
  game day needs a real channel to verify alert routing end to end).

### 5. Allow-list contract

- The harnesses in this repo read `TARGET_ALLOWLIST` (comma-separated
  `host:port` entries). The staging declaration MUST name the exact entries to
  place there (e.g. `staging.example.com:443`) and confirm production is never
  listed.

---

## Current state (2026-11-12)

- **Gate: NOT met.** No staging environment exists or is declared.
- **What runs instead:** local-topology harnesses with every result marked
  UNVERIFIED and this gate named. The game day verifies alert routing to a
  **local alert sink** only; the real on-call channel step is blocked on this
  gate.
- **Owner of this gate:** platform/ops. Engineering has the harnesses ready to
  point at any declared target.

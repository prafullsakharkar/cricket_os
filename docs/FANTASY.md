# FANTASY.md — optional fantasy scoring (Phase H)

Fantasy statistics compute **per-player points** for a match under a
**versioned, frozen ruleset**. This module is optional and is shipped
**DRAFT-only** until the product/legal gate below is satisfied. It is not a
gambling product: contests, wagering, wallets, payments, user teams/entries,
captain multipliers, user leaderboards and notifications are **out of scope**
(see the plan pre-flight ruling).

> **Product/legal gate.** `docs/FANTASY_PRODUCT_DECISION.md` is **ABSENT**.
> Consequences (enforced, not aspirational):
>
> * every shipped ruleset is a **DRAFT** (`status = DRAFT`) — nothing is
>   frozen, and drafts are never served by the public API;
> * `FANTASY_PUBLIC_API_ENABLED=false` by default — the read endpoints 404 at
>   the URL level when the flag is off, and each view re-checks the flag;
> * seed/illustrative rulesets, when added, are round-number DRAFTs labelled
>   **"requires product-owner approval before freezing"** and are **never
>   frozen by a seed**;
> * the project **never copies a commercial operator's point tables**
>   (Dream11, ESPN, etc.). Rulesets are authored in-house.

---

## 1. What it computes

One **ruleset version** = a declarative list of **point rules** + a set of
**policy flags**. A run computes, for every player in a match, a
`total_points` (Decimal, 2 dp, `ROUND_HALF_UP`) and a per-metric `breakdown`.

Points are **derived** from the same canonical statistics rows the Phase F
aggregates consume (`PlayerMatchStat`, match result/awards, playing-XI) —
**never** from `delivery` ball data, and never from a third party's points.

### 1.1 Rule vocabulary (worked examples)

A rule is `(metric, operator, points, scope, condition)`:

| Metric | Scope | Operators | Example |
|---|---|---|---|
| `runs` | batting | `per_unit`, `threshold_bonus`, `tiered`, `capped` | 1 pt per run → `runs / per_unit / 1.00` |
| `fours` / `sixes` | batting | `per_unit`, `threshold_bonus`, `tiered` | 1 pt per four, 2 per six |
| `balls` | batting | `per_unit` (min qualifier) | 0.10 per ball faced (needs ball data) |
| `duck` | batting | `flag` | −2 for a duck (boolean, needs dismissal kinds) |
| `strike_rate_band` | batting | `tiered` (min balls) | SR ≥ 150 → +10 (needs ball data) |
| `run_milestone` | batting | `threshold_bonus` | 50 → +10, 100 → +20 |
| `wicket` | bowling | `per_unit`, `threshold_bonus`, `tiered` | 20 per wicket |
| `bowled_lbw_bonus` | bowling | `per_unit` | +5 extra (needs dismissal kinds) |
| `maiden` / `dot_ball` | bowling | `per_unit` | 8 per maiden (needs ball data) |
| `economy_band` | bowling | `tiered` (min overs, lower better) | econ ≤ 5 → +10 |
| `hat_trick` | bowling | `flag` | +25 |
| `catch` / `stumping` / `run_out` | fielding | `per_unit` | 10 per catch (needs fielding data) |
| `run_out_direct` / `run_out_indirect` | fielding | `per_unit` | split direct/relay credit (needs delivery-level attribution) |
| `playing_xi` | participation | `flag` | +4 for being in the XI |
| `player_of_match` | participation | `flag` | +20 |

**Worked example** (illustrative, round numbers, DRAFT only): *"1 point per
run, 10 per wicket, 10 per catch, +4 playing XI"* is three `per_unit` rules
plus one `flag` rule. A player who scores 58, takes 0 wickets, 1 catch, and is
in the XI earns `58 + 0 + 10 + 4 = 72.00`.

Operators:

* `per_unit` — points × the metric value (a cap may bound it: `capped`).
* `threshold_bonus` — a fixed bonus when a threshold is crossed.
* `tiered` — points by tier (`tiers=[[min, points], ...]`); rate metrics
  (`strike_rate_band`, `economy_band`) must declare a qualifier
  (`min_balls` / `min_overs`) and order tiers easiest→hardest.
* `flag` — a boolean metric (duck, playing_xi, hat_trick, player_of_match).

Validation is **whole-version**: a version is either valid as a whole or not
stored. Contradictory rules (e.g. `per_unit` + `capped` on the same
metric/scope) are rejected at authoring time and by the pipeline.

### 1.2 Data requirements & unsupported rules

Each metric declares the source inputs it needs (closed registry in
`apps/fantasy/engine/metrics_registry.py`). When a match's snapshot lacks an
input, the metric's rules are **skipped and reported** in
`unsupported_rules` — never guessed. The result is flagged `is_partial`.

| Data the snapshot needs | Metrics it unlocks |
|---|---|
| `PlayerMatchStat` batting counts (runs, fours, sixes) + playing XI | `runs`, `fours`, `sixes`, `run_milestone`, `playing_xi` |
| Ball-by-ball data (balls faced/bowled, maidens, dots, economy) | `balls`, `strike_rate_band`, `maiden`, `economy_band`, `dot_ball` |
| Dismissal kinds (bowled/lbw/caught/…, ducks) | `duck`, `bowled_lbw_bonus` |
| Fielding counts (catches, stumpings, run-outs) | `catch`, `multi_catch_bonus`, `stumping`, `run_out` |
| Delivery-level run-out attribution (direct vs relay) | `run_out_direct`, `run_out_indirect` |
| Match awards | `player_of_match` |
| Bowling summary (wickets, hat-trick) | `wicket`, `wicket_haul`, `hat_trick` |

**Unsupported-rule metrics today** (and what data would unlock them): a
summary-only scorecard (`is_partial`) has no ball data, so `balls`,
`strike_rate_band`, `maiden`, `economy_band`, `dot_ball` are unsupported;
without fielding counts `catch`/`stumping`/`run_out` are unsupported; without
delivery-level attribution `run_out_direct`/`run_out_indirect` are
unsupported. Loading full ball-by-ball data (Phase C importers already do for
complete scorecards) and fielding credit types unlocks them.

### 1.3 Policy flags

Every version carries policy flags that change **how the snapshot is
interpreted** (merged with the engine defaults; stored overrides win):

* `super_over` — `exclude` (default): super-over rows are excluded from the
  match snapshot.
* `abandoned` — `no_points` (default): no points are computed for
  abandoned/no-result matches.
* `reduced_overs` — `compute` (default): points are computed as normal for
  rain-reduced matches.
* `retirement_hurt` — `not_out` (default): an injured retirement is scored as
  not-out.
* `substitutes` — `exclude` (default): substitute fielders earn no points.

---

## 2. Versioning & freeze lifecycle

* **Draft → frozen → (retired).** A version is authored as a DRAFT, validated
  (`validate_version` re-uses the engine's `ruleset_spec_for`), dry-run
  (`dry_run_version` reports the per-player diff without writing), then
  **frozen** by an admin. Freezing records `content_hash` (the canonical
  digest of every rule + parameter) and makes the version **immutable** at the
  database level (UPDATE/DELETE/INSERT blocked by a trigger once frozen) and
  at the service level.
* **Changing rules means a new version.** A frozen version is never edited.
  `retire_version` **forks a new draft** (copies rules/policies/format, next
  version number); the old version stays as the audit anchor for every run
  that used it.
* **Defaults.** `set_default_version` marks the at-most-one default per
  (ruleset, format) and must run on a **draft** (a frozen version cannot be
  updated); the draft freezes with `is_default` intact, so a default is
  permanent once frozen.
* **Pipeline selection.** The Tier-2 pipeline computes every version with
  `auto_recompute=True` on an active ruleset (draft or frozen) for each
  completed match; `version_id=` forces one version.

---

## 3. Computation, ledger & reproducibility

### 3.1 The run (calc audit)

Every compute is one **calc run** recorded in `fantasy_calc_audit`:

* the **snapshot** (compressed canonical rows the engine consumed),
  `snapshot_digest`, and `output_digest` — so any run can be **replayed**;
* `version_hash` (the frozen ruleset digest) and `engine_version`
  (`FANTASY_ENGINE_VERSION`);
* `trigger` (`match.completed`, `correction.applied`, `player.merged`,
  `sweep`, `backfill`, `provisional`, …), `is_provisional`, timing, and the
  per-match advisory-lock serialisation.

**Idempotence.** A run whose latest audit already has the same
`(snapshot_digest, version_hash, engine_version)` is a no-op — nothing is
written. Recompute-from-source (never delta) makes the pipeline
order-independent and corrections/backfills byte-identical.

### 3.2 Append-only ledger

`fantasy_points_ledger` is **append-only**: UPDATE and DELETE are blocked by a
database trigger for every role. Each changed run appends one row per player
with `calc_run_id`. **Current points are derived** — the latest non-provisional
audit's rows — never stored as a mutable total. `is_current`/`superseded_by`
are informational; rows are never mutated.

### 3.3 Adjustments

A manual correction is an **adjustment** — its own append-only table
(`fantasy_point_adjustment`), role-gated (`data_steward`/admin), bounded
(`|delta| ≤ 1000.00`, 2 dp `ROUND_HALF_UP`, non-zero), with a mandatory
reason + reference, and audited. **Voiding appends an offsetting entry**
(`delta=-adj.delta`, `reference=<adj id>`) — the original is never edited.

> Combined points = computed points + net adjustments. The read model
> (`player_match_performance`) folds the adjustment sum in, and it survives
> recomputes.

### 3.4 Reproducibility guarantee

`python manage.py fantasy_verify <scope>` replays every audit from its stored
snapshot and checks `snapshot_digest`, `output_digest`, the version hash, and
that the current ledger sums agree. Any finding is corruption or a bug — never
a normal supersession. Exits non-zero on findings.

---

## 4. Live vs final (provisional)

* **Final** runs are the default: written to the ledger + read model, cached,
  and published on `fantasy.computed`.
* **Provisional** runs (`provisional=True`, behind
  `FANTASY_PROVISIONAL_ENABLED`, default off) write **only** a labelled calc
  audit (`is_provisional=True`) — never the final ledger/read model — for
  throttled, mid-match previews. `ProvisionalDisabledError` is raised when the
  flag is off.
* The public API labels every payload `status: final|provisional` and
  `is_partial`, plus the exact `calc_run_id` and ruleset `{id, version, hash}`
  that produced it.

---

## 5. Public API (flag-gated)

Registered in `config/urls.py` only when `FANTASY_PUBLIC_API_ENABLED` is on
(404 at the URL level when off; each view also re-checks the flag):

| Endpoint | Purpose |
|---|---|
| `GET /fantasy/rulesets` (+ `/{id}/versions/{version}`) | public (non-draft) ruleset catalogue, `max-age=3600`, `?fields=` sparse |
| `GET /fantasy/points` | per-player rows, page-number pagination, `match`/`player`/`ruleset_version` filters (≥1 required), ETag/304 |
| `GET /fantasy/matches/{id}` | all players for the primary version, ordered by `-total_points`, `max-age=120` |

Every response carries `meta`: `label: "derived"`, the third-party notice
("Points are computed under the stated ruleset and are not any third party's
official fantasy points"), `calc_run_id`, `status`, `is_partial`,
`unsupported_rules`, `as_of`, and `ruleset {id, version, hash}`. **Drafts are
never public.** Merged players answer 301 to the canonical id. Full detail:
`docs/CRICKET_API_SPECIFICATION.md` §2.6.

---

## 6. Observability

Metrics (in `apps/fantasy/metrics.py`, rendered by the internal `/metrics`):
`fantasy_recompute_lag`, `fantasy_queue_depth`, `fantasy_sweep_findings`,
`fantasy_recompute_seconds` (histogram), `fantasy_recompute_failures_total`,
`fantasy_partial_rate`, `fantasy_unsupported_rule_counts{metric}` (bounded
label), `fantasy_ledger_growth`, `fantasy_verify_mismatches_total`. Alerts in
`ops/alerts/fantasy_alerts.yml` (`FantasyStaleCalc`, `FantasyRecomputeFailures`,
`FantasyVerifyMismatch`); runbooks in `docs/RUNBOOKS.md` (`## Fantasy
pipeline`, `## Fantasy points dispute`).

---

## 7. Retention of stored snapshots

Every calc audit stores the compressed input snapshot so runs are
reproducible indefinitely. Snapshots are stored in the audit row (not a
separate bucket) and are subject to the same backup policy as the database
(`docs/CRICKET_SECURITY_AND_RELIABILITY.md` §6). The append-only tables are
**never purged or compacted**; retention is the database's.

---

## 8. Out of scope (explicitly)

Contests, wagering/wallets/payments, user teams/entries, captain/VC
multipliers, user leaderboards, notifications, and any "official" third-party
points. Fantasy here is **computed player points only**, and nothing in this
module can be used to settle a contest.

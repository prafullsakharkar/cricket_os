# CRICKET_TESTING_STRATEGY.md

Testing plan for Cricket OS. Sources of truth: the **Laws of Cricket** (MCC),
**ICC playing conditions**, and the selected **licensed provider spec**. The
scoring engine is pure and is the primary target of fast unit testing.

---

## 1. Test pyramid & tooling

| Level | Scope | Tools | Share |
|---|---|---|---|
| Unit | Pure rules, serializers, utilities | `pytest`, `hypothesis` (property-based) | ~60% |
| Integration | Models, services, DB constraints, rebuild pipeline | `pytest-django`, `factory_boy`, disposable Postgres | ~25% |
| Contract | Provider adapters, API responses | `schemathesis`, `jsonschema`, recorded fixtures | ~8% |
| E2E | Match lifecycle, realtime, frontend-facing flows | `pytest` + `channels.testing` + `httpx`/Playwright | ~5% |
| Performance | Hot queries, load, realtime | `pytest-benchmark`, `k6` | separate |
| Migration | Forward/backward migrations at scale | `pytest` + prod-like fixtures | CI gate |

**Determinism:** freeze time; generate UUIDv7 with a seeded clock; use
recorded provider fixtures (no live network in CI).

---

## 2. Unit tests (scoring rules)

Covered in [CRICKET_SCORING_RULES.md §5](CRICKET_SCORING_RULES.md). Additional
property-based tests:

```python
@given(runs=st.integers(0,1000), innings=st.integers(1,50), not_outs=st.integers(0,50))
def test_batting_average_never_errors(runs, innings, not_outs):
    not_outs = min(not_outs, innings)
    avg = batting_average(runs, innings, not_outs)
    assert avg is None or avg >= 0

@given(st.lists(delivery_strategy(), min_size=0, max_size=72))
def test_innings_totals_equal_sum_of_parts(deliveries):
    totals = innings_totals(deliveries, RuleConfig())
    assert totals.runs == sum(d.total_runs for d in deliveries)
    assert totals.extras['total'] == sum(d.extras_component() for d in deliveries)
```

---

## 3. Integration tests (DB models & rebuild)

- **Constraint tests:** inserting a wicket delivery without `dismissal_type`
  raises; the partial unique index `ux_delivery_current` rejects a **second**
  `is_current` row for the same `(match_date, innings_id, delivery_seq)` while
  `ux_delivery_revision` admits a higher revision; duplicate provider entity map
  raises; `h2h` order CHECK enforced. For `innings_event`:
  `ux_innings_event_current` rejects a second current row for the same
  `(innings_id, event_type, player, anchor)`, `ux_innings_event_revision` admits a
  higher revision, `ux_innings_event_provider` is nullable-safe, and
  `ck_innings_event_player` rejects a non-`OTHER` event with a null `player_id`.
- **Rebuild determinism:** build an innings twice from the same deliveries →
  identical `batting_entry`/`bowling_entry`/`innings_summary` digests.
- **Incremental correctness:** append a delivery and assert only affected
  aggregates change (no full-table churn).
- **Tier-1 only per delivery:** appending a delivery updates
  `innings_summary`/`batting_entry`/`bowling_entry` (and FOW/partnership/over)
  but leaves `player_match_stat`, `player_career_stat`, `team_stat`,
  `tournament_standings` and `leaderboard` **untouched** — no career/team
  recompute per ball. Tier 2 runs on `match.completed` / `correction.applied`.
- **Revision bumping:** a correction appends a higher `revision`, flips the
  previous row to `is_current=false`/`superseded_at`, and preserves old values in
  `DataCorrection`.

---

## 4. API contract tests

- **Schema validation:** every endpoint response validated against a JSON Schema
  (`schemathesis` against the OpenAPI spec).
- **Stability:** golden files for representative payloads (live, scorecard,
  player stats, fixtures) compared on regression.
- **Conventions:** UTC timestamps, cursor stability, error envelope, `ETag`/`304`
  behaviour, `403 license_required` when commentary is unlicensed.
- **State-dependent caching:** a match sub-resource serves `Cache-Control:
  max-age=5, swr=5` while the match is live and switches to `max-age=300,
  s-maxage=3600` with a **strong** `ETag` once it is `COMPLETED`; applying a
  **correction** changes the `ETag` (purge by tag), and `immutable` appears only
  after the post-completion reconciliation window.
- **Backward compatibility:** additive-only within `v1`; `Sunset` headers on
  deprecations.

---

## 5. Provider adapter tests

- **Contract tests** per adapter using recorded fixtures for each provider:
  happy path, partial payload, unknown enum, missing optional field, rate-limit
  response, 5xx, malformed JSON.
- Adapters must never raise on unknown fields; they quarantine.
- Golden mapping: `RawDelivery → canonical Delivery` snapshot tests.

---

## 6. Ingestion idempotency tests

```python
def test_replaying_same_delivery_is_noop(db, provider):
    ingest(provider, delivery_fixture(seq=42))
    ingest(provider, delivery_fixture(seq=42))       # replay
    assert Delivery.objects.filter(innings=..., delivery_seq=42).count() == 1

def test_job_idempotency_key_prevents_duplicate_runs(db):
    a = enqueue_import(idempotency_key="m:147942:scorecard:r1")
    b = enqueue_import(idempotency_key="m:147942:scorecard:r1")
    assert a.id == b.id   # collapsed
```

## 7. Duplicate & out-of-order event tests

```python
def test_out_of_order_arrival_does_not_corrupt_state(db, provider):
    ingest(provider, d(seq=10)); ingest(provider, d(seq=8))   # 8 arrives late
    # state is rebuilt from stored deliveries, ordered by seq
    assert innings_score(innings) == expected_from_seqs([8,10])

def test_gap_detected_by_reconciliation(db, provider):
    ingest(provider, d(seq=1)); ingest(provider, d(seq=3))
    report = reconcile_innings(innings)
    assert report.has_gaps and report.missing == [2]
```

## 8. Match-correction tests

```python
def test_correction_changes_scorecard_and_is_audited(db, provider):
    ingest_full_innings()
    before = score_digest(innings)
    apply_correction(delivery_seq=30, field="runs_off_bat", old=2, new=4)
    assert score_digest(innings) != before
    assert DataCorrection.objects.filter(entity_id=innings.id).exists()
    current = Delivery.objects.get(match_date=md, innings=innings, delivery_seq=30, is_current=True)
    assert current.runs_off_bat == 4
    prior = Delivery.objects.get(match_date=md, innings=innings, delivery_seq=30, revision=1)
    assert prior.runs_off_bat == 2 and prior.is_current is False   # superseded
    # exactly one current row per ball (partial unique index)
    assert Delivery.objects.filter(
        match_date=md, innings=innings, delivery_seq=30, is_current=True).count() == 1
```

## 9. Historical import tests

- Import a Cricsheet JSON/YAML match → assert matches/innings/deliveries/scorecard
  counts and totals.
- Import a **summary-only** historical match (no deliveries) → `batch_complete=false`,
  ball endpoints return `meta.ball_data=false`, career aggregates computed and
  marked partial.
- Import the **people/venues register** → `provider_entity_map` rows created;
  re-import is idempotent.

## 10. Cache invalidation tests

```python
def test_delivery_appended_invalidates_match_scorecard(redis, db):
    prime_cache("match:1:scorecard", {...})
    append_delivery(match_id=1)
    assert cache_get("match:1:scorecard") is None
def test_match_completed_invalidates_series_points_table(redis, db):
    prime_cache("series:9:points", {...})
    complete_match(match_series=9)
    assert cache_get("series:9:points") is None
```

## 11. Realtime delivery tests

- Using `channels.testing.WebsocketCommunicator` (driven by the **ReplayAdapter**
  replaying a recorded match through the live pipeline): subscribe to
  `match:{id}`; append a delivery; assert the client receives exactly one
  `delivery.appended` with a monotonic **per-match** `seq` (from
  `match_event_log`).
- **Reconnect/replay:** drop and reconnect with `last_seq`; assert the server
  replays the retained `match_event_log` rows with no gap and no duplicate.
- **`resync_required`:** with a `last_seq` older than the retained window (or the
  log purged after completion), assert the server returns `resync_required`.
- **Coalescing:** rapid deliveries produce ≤1 update/s/client.
- **SSE parity:** the SSE stream yields the same event sequence.

## 12. Performance tests

- Hot-query benchmark suite with regression thresholds (see performance doc §12).
- `k6` scenarios: read soak, marquee spike, deliveries feed, ingestion throughput.
- CI gate on `EXPLAIN` plans for the top-10 queries.

## 13. Migration tests

- Apply all migrations forward on a prod-like dataset; assert no long locks.
- For each migration, assert the documented rollback path works.
- `AddIndexConcurrently` used for large indexes; test that it does not block writes.

### 13.1 Reference-DDL ↔ migration drift check (CI gate)

`CRICKET_DATABASE_SCHEMA.md` is the **source of truth**; Django migrations must
produce a schema indistinguishable from it. A CI job builds a scratch database
**from the reference DDL**, then builds a second scratch database **from the
Django migrations only**, and diffs the two introspections. Any difference fails
the build. The diff covers:

- **Indexes** — full name + definition, including **partial-index predicates**
  (`pg_index.indpred`) and `INCLUDE` columns. Expression/partial indexes such as
  `ux_delivery_current`, `ux_innings_event_current` and the trigram indexes must
  match exactly.
- **Constraints** — primary keys, unique constraints, `CHECK` constraints
  (expression text normalised) and foreign keys (including `ON DELETE`).
- **Enums** — every label and label order in `pg_enum`.
- **Partitioning** — `delivery` must be reported as partitioned
  (`pg_partitioned_table`), with partition bounds matching.

Because several objects cannot be expressed by the ORM, they are hand-written and
the drift check is what keeps them honest:

- `delivery` is declared `Meta.managed = False`; its `PARTITION BY RANGE
  (match_date)` definition, partitions and the partial/unique indexes are created
  by a hand-written **`RunSQL`** migration.
- Enum types, partial and expression indexes, and `CHECK` constraints are
  hand-written `RunSQL` migrations (the ORM cannot emit them portably).

---

## 14. End-to-end match-lifecycle fixtures (required scenarios)

These run end-to-end through the live pipeline driven by the **ReplayAdapter**
(recorded Cricsheet matches), so Phases D/E can be exercised and load-tested
before a licensed live feed exists.

### 14.1 Test match with multiple innings

- 4 innings; follow-on applied; a declaration; a draw (time expires).
- Assertions: innings numbering, `is_follow_on_innings`, `declared`, result
  `DRAW`, and that career aggregates span two innings by the same player.

### 14.2 ODI with a revised target (DLS)

- Rain reduces Team 2's overs; `dls_target` is set by the **provider/licensed**
  source (`source=provider`), never computed by the platform.
- Assertions: chasing side wins by `wickets` using the **revised** target;
  `meta.dls=true`; NRR uses revised figures per playing conditions.
- **Never** assert a *self-computed* DLS value — only the provider/licensed value
  is stored and displayed.

### 14.3 T20 with a Super Over

- Regulation tied; Super Over decides.
- Assertions: Super Over stored as separate innings (`is_super_over=true`);
  result `SUPER_OVER_TIE_BREAK`; Super Over runs excluded from career/NRR by
  default; a second Super Over increments `super_over_number`.

### 14.4 Wides and no-balls

- A sequence including a wide, a wide+bye, a no-ball off the bat, a no-ball+byes.
- Assertions: legal-ball counting, over completion, batter balls-faced,
  bowler runs charged, innings total, extras breakdown.

### 14.5 Run-outs and other dismissals

- A run-out (no credited bowler), a stumped, a caught, an LBW, a retired-hurt
  (not out, resumes later) and a retired-out.
- Assertions: field-of-wicket numbering; the wicket count **excludes** the
  retired-hurt/not-out (`is_wicket=false`, `retirement_type` set) and **includes**
  the retired-out (`is_wicket=true`, `dismissal_type=RETIRED_OUT`); the
  retired-hurt batter counts as **not-out** in averages and adds **no** FOW row;
  bowler average excludes run-outs; `fielder1..3` captured.
### 14.6 Abandoned match

- No play after toss → `ABANDONED`/`NO_RESULT`; NRR contributes nothing.

### 14.7 Incomplete historical scorecards

- Summary-only import; assert reduced API surface and `estimated` labelling of
  ball-dependent metrics.

### 14.8 Provider correction after match completion

- Completed match corrected post-hoc.
- Assertions: `DataCorrection` recorded; scorecard rebuilt; standings/leaderboard
  recomputed; `correction.applied` published; caches invalidated; audit chain
  intact.

### 14.9 Off-ball retirement (rain / break)

- A batter retires hurt during a rain delay (no delivery to attach to), and a
  batter retires not-out during an innings break; one later resumes.
- Assertions: the retirements are stored as `innings_event`
  (`event_type='RETIREMENT_HURT'|'RETIREMENT_NOT_OUT'`, `anchor_delivery_seq`
  `NULL` or the delivery they follow) and **not** on any `delivery`; the merged
  accessor `innings_retirements(innings)` returns each exactly once; the wicket
  count is unchanged; the partnership breaks and a new partnership opens with the
  incoming batter and no `fall_of_wicket` row; the API `retirements[]` surfaces
  them with `anchor` and `resumed`; a boundary check rejects the same retirement
  appearing in **both** `delivery` and `innings_event`.

### 14.10 The Hundred (5-ball sets, 100 balls)

- A full Hundred innings driven through the ReplayAdapter.
- Assertions: a set completes after **5** legal balls; `overs_display` uses the
  5-ball unit (`1.2` after 7 balls, `20.0` for a full innings); economy is runs
  per 5-ball set; a maiden is 5 legal balls with zero runs charged; the innings is
  `100` balls (`overs_limit × balls_per_over`); the 10-ball end change has **no**
  effect on strike rotation or stored columns (presentation only).

---

## 15. Definition of a well-tested change

A change is "done" only when: unit tests for new rules, an integration test for
new persistence, a contract test if it touches the API, an idempotency test if it
touches ingestion, and a migration test if it touches the schema — all green in
CI, with no reduction in coverage of `scoring/`. A schema change additionally
requires a green reference-DDL ↔ migration drift check (§13.1).

---

## 16. CI database jobs

Both jobs below use the **full `postgres:16` image**, which bundles contrib. The
slim/alpine variants omit `pg_trgm`/`btree_gin`, so a slim image would make the
DDL job pass locally while the real schema (which needs the trigram indexes)
fails — always pin the full image. These are documented as app-workflow
specifications (the repository's `.github/workflows/` is created with the
application scaffold).

### 16.1 `schema-ddl` — reference DDL applies cleanly on PostgreSQL 16

Extracts the ```sql blocks from `CRICKET_DATABASE_SCHEMA.md` (skipping the §3.9
example-query block, which uses `$1` placeholders) and applies them with
`ON_ERROR_STOP=1`, then asserts the extensions and trigram indexes exist.

```bash
# service: postgres:16  (env POSTGRES_PASSWORD=postgres, health-cmd pg_isready)
psql -v ON_ERROR_STOP=1 -U postgres -d cricket -c \
  "CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS btree_gin;"

awk '/^### 3\.9/{stop=1} stop{next}
     /^```sql$/ {inb=1; next}
     /^```$/ && inb {inb=0; print ""; next}
     inb {print}' docs/CRICKET_DATABASE_SCHEMA.md > /tmp/schema.sql

psql -v ON_ERROR_STOP=1 -U postgres -d cricket -f /tmp/schema.sql   # 0 errors

# Assert the extensions and the trigram indexes the planner relies on:
psql -v ON_ERROR_STOP=1 -U postgres -d cricket -c "
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname IN ('pg_trgm','btree_gin'))
      THEN RAISE EXCEPTION 'contrib extensions missing'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes
                   WHERE indexname IN ('ix_team_name_trgm','ix_player_name_trgm'))
      THEN RAISE EXCEPTION 'trigram indexes missing'; END IF;
  END \$\$;"
```

### 16.2 `migration-drift` — reference DDL vs. Django migrations

Runs the §13.1 drift check: build one scratch database from the reference DDL and
one from the migrations only, introspect both, and fail on any difference in
indexes (incl. partial predicates and `INCLUDE` columns), constraints, enums
(labels and order) or partitioning.

---

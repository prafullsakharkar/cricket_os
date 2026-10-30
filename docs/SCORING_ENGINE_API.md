# SCORING_ENGINE_API.md

Public surface of the **pure scoring engine** (`apps/scoring/`, Phase A+).

The engine is a **pure function of deliveries + rule config**: no database, no
ORM, no clock, no randomness, no network. It is deterministic — the same inputs
always produce the same outputs — so corrections and historical rebuilds are
reproducible (SCORING_RULES §1, TESTING_STRATEGY §1). `apps/scoring` imports
nothing but the standard library and `apps.core`-free code; a contract test
blocks Django at import time (`tests/scoring/test_purity.py`).

---

## 1. Boundaries

| Rule | Enforcement |
|---|---|
| Inputs are frozen, typed dataclasses | `DeliveryRecord`, `InningsEvent`, `InningsContext`, `MatchContext` |
| Outputs are frozen, typed dataclasses | every `*Result`, `*Line`, `*Totals` type below |
| Reject bad input, never coerce | typed `ScoringInputError` subclasses carry the offending `delivery_seq` |
| Only current rows | non-`is_current` deliveries/events raise `SupersededDataError` |
| Order by `delivery_seq` | input order is ignored; rows are sorted deterministically |
| Reject duplicates | duplicate `(innings_id, delivery_seq)` → `DuplicateDeliveryError` |
| No division-by-zero, no `inf` | `safe_div` returns `None` for every undefined ratio |
| DLS is never computed | `dls.compute_target` raises `LicensedMethodRequired` |
| Versioned rules | every output records `rule_version` |

Identifiers are opaque strings (the persistence layer maps them to `uuid`s).

---

## 2. Inputs

### `DeliveryRecord` (frozen)
Mirrors DATABASE_SCHEMA §3.6 `delivery`:

```python
@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    innings_id: str
    delivery_seq: int          # authoritative order, >= 1
    over_number: int
    ball_in_over: int          # legal-ball counter within the over
    bowler_id: str
    batter_id: str
    non_striker_id: str
    runs_off_bat: int = 0
    wides: int = 0
    noballs: int = 0
    byes: int = 0
    leg_byes: int = 0
    penalty_runs: int = 0
    is_wicket: bool = False
    dismissal_type: DismissalType | None = None
    dismissed_player_id: str | None = None
    bowler_credited_wicket: bool = False
    fielder1_id: str | None = None
    fielder2_id: str | None = None
    fielder3_id: str | None = None
    retired_player_id: str | None = None
    retirement_type: RetirementType | None = None
    revision: int = 1
    is_current: bool = True
```

Derived (computed, never stored twice): `total_runs`, `extras_component`,
`is_wide`, `is_noball`, `is_legal` (= `wides == 0 and noballs == 0`).

**Wicket invariant:** `is_wicket=True` only for team-tally dismissals plus
`RETIRED_OUT`; `HURT`/`NOT_OUT` retirements are `is_wicket=False`. A wicket
requires `dismissal_type` + `dismissed_player_id`; a non-wicket forbids them.

### `InningsEvent` (frozen)
Off-ball events: `RETIREMENT_HURT`, `RETIREMENT_NOT_OUT`, `RESUME`, `OTHER`.
`anchor_delivery_seq` is the delivery the event **follows** (`None` = before the
first ball).

### `InningsContext` (frozen)
Metadata + rows for one innings:

```python
innings_id, innings_number (1..4), batting_team_id, bowling_team_id,
is_super_over, super_over_number, declared, forfeited, all_out,
target, target_balls, dls_target, is_follow_on_innings, status,
deliveries: tuple[DeliveryRecord, ...], events: tuple[InningsEvent, ...]
```

### `MatchContext` (frozen)
```python
match_id, format: Format, status: MatchStatus, teams: tuple[str, str],
overs_limit, balls_per_over, is_follow_on, is_dls_applied, time_expired, result_type
```

### `RuleConfig` (frozen, versioned)
See SCORING_RULES §1. Presets: `icc-test-2024`, `icc-odi-2024`,
`icc-t20i-2024`, `the-hundred`, plus `preset_for_format()` and the
`FORMAT_CATALOGUE` seed (DATABASE_SCHEMA §3.2). `RuleConfig.version` is required
and is stamped on every output.

---

## 3. Entry points

| Function | Returns | Notes |
|---|---|---|
| `rebuild_innings(context, config, *, estimated=False)` | `InningsResult` | Totals, batting/bowling lines, FOW, partnerships, overs, retirements |
| `rebuild_match(match, innings_contexts, config, *, estimated=False)` | `MatchScorecard` | All innings + `decide_match_result` |
| `decide_match_result(match, innings_results, config)` | `MatchResult \| None` | `None` while in progress |
| `build_partnerships(context, config)` | `(partnerships, fows)` | Low-level |
| `innings_retirements(deliveries, events, rule_version)` | `tuple[RetirementRecord, ...]` | Merged on-ball + off-ball, each exactly once |
| `career_totals / player_match_line / team_match_line / season_totals / net_run_rate / build_standings` | aggregate types | Tier 2 |
| `dls.*` | `DlsTarget` | provider/licensed values only |

---

## 4. Outputs and metric classification

Every output carries `rule_version`. Fields marked **derived/estimated** are
labelled at runtime via `Classified.classification_of(field)` /
`Classified.derived_fields()`; everything else is **official** (SCORING_RULES §4).

| Type | Official | Derived/estimated |
|---|---|---|
| `ExtrasBreakdown` | wides, noballs, byes, leg_byes, penalty, total | — |
| `Totals` | runs, wickets, legal_balls, extras, overs_display, run_rate | `estimated` |
| `BattingLine` | runs, balls, 4s/6s/1s/2s/3s/5s, dots, out, dismissal fields, strike_rate | `dot_pct`, `boundary_pct`, `estimated` |
| `BowlingLine` | legal_balls, runs_conceded, wickets, maidens, wides, noballs, dots, overs_display, economy, bowling_avg, strike_rate | `estimated` |
| `FallOfWicket` | wicket_number, score, over_display, delivery_seq, dismissed_player_id | — |
| `Partnership` | wicket_number (ordinal), batters, runs, balls, boundaries, seqs, unbroken | — |
| `OverSummary` | over_number, bowler_id, legal_balls, runs_conceded, wickets, is_maiden, is_complete, deliveries_count | — |
| `InningsResult` | totals, lines, FOW, partnerships, overs, retirements, flags | `estimated` |
| `MatchResult` | result_type, winner, margin, is_dls, is_super_over, result_text | — |
| `MatchScorecard` | innings, result | `estimated` |
| `PlayerMatchLine` | per-match batting/bowling facts | — |
| `TeamMatchLine` | runs/balls/all-out/result for points & NRR | — |
| `SeasonTotals` | matches/w/l/t/d/nr, points, runs, balls | `nrr` (official stat) |
| `StandingsRow` | position, played, w/l/t/d/nr, points, nrr | — |
| `CareerTotals` | matches, innings, not_outs, runs, high_score, 50s/100s, averages | — |
| `RetirementRecord` | player, kind, anchor, source, resumed | — |
| `DlsTarget` | target, method_version, source (`provider`/`licensed`), par_score, balls | — |

Undefined ratios are always `None` (rendered `"—"`): batting average when never
out, bowling average/SR with 0 wickets, SR/economy with 0 balls, NRR with no
overs, required run rate with 0 balls remaining.

---

## 5. Rules implemented (SCORING_RULES §2–§3)

- **Legal balls / overs:** `is_legal` = no wide/no-ball; over completion and
  `overs_display` key off `balls_per_over` (E25 — The Hundred is 5-ball sets);
  `ball_in_over` is informational.
- **Totals:** runs = Σ `total_runs`; wickets = distinct `is_wicket` dismissals
  (capped at `max_wickets = team_size - 1`); extras split by type.
- **Batter:** faces legal + (optional) no-balls, never wides; runs are
  `runs_off_bat`; strike/dot/boundary rates are `None` with 0 balls (E1, E3).
- **Bowler:** charged `runs_off_bat + wides + noballs`; byes/leg-byes/penalties
  excluded (E7, E8); credited wickets exclude run-outs (E9); maiden = a complete
  over with 0 charged runs.
- **Retirements:** `HURT`/`NOT_OUT` are non-wicket events (no FOW, break the
  partnership, count as not-out, may resume); `RETIRED_OUT` is a dismissal with a
  FOW row (E10, E11). On-ball and off-ball retirements merge through
  `innings_retirements`; the same retirement in both sources raises
  `RetirementSourceConflictError`.
- **Results:** precedence ABANDONED/CANCELLED/POSTPONED → NO_RESULT;
  FORFEITED → FORFEIT; time-expired (with `allows_draw`) → DRAW; target reached →
  WIN by wickets; all-out/overs-complete → TIE or WIN by runs; Test aggregate
  logic handles follow-on, declarations, innings margins, and draws (E12–E17).
- **DLS:** revised target comes only from the provider (`source="provider"`).
  `decide_match_result` raises `MissingDlsTargetError` if `is_dls_applied` has no
  target; `compute_target` always raises `LicensedMethodRequired` (E18).
- **Super Over:** separate innings (`is_super_over`, `super_over_number`), result
  `SUPER_OVER_TIE_BREAK`, excluded from career/NRR by default (E19).
- **Reduced overs:** `MatchContext.overs_limit` (or `target_balls`) drives quota
  completion and NRR (E20).
- **NRR:** bowled-out side is charged the full entitled quota (E14); declared
  innings use actual overs (E12); no-result and super-over contribute nothing by
  default.
- **Partial data:** `estimated=True` propagates to outputs; ball-dependent
  metrics are never fabricated (E23).

---

## 6. Errors

All input errors derive from `ScoringInputError(ValueError)` and expose
`delivery_seq` / `innings_id` where applicable:

`InvalidDeliveryError`, `DuplicateDeliveryError`, `OutOfOrderDeliveryError`,
`SupersededDataError`, `InvalidInningsError`, `InvalidEventError`,
`DataQualityError`, `RetirementSourceConflictError`, `InvalidStatInputError`,
`MissingDlsTargetError`. Non-input failures: `LicensedMethodRequired`.

---

## 7. Phase C mapping

| Phase C artifact | maps to |
|---|---|
| `delivery` rows → engine | `DeliveryRecord` (is_current only) |
| `innings_event` rows | `InningsEvent` |
| `innings` metadata | `InningsContext` |
| `match` row | `MatchContext` |
| `innings_summary` | `InningsResult.totals` |
| `batting_entry` / `bowling_entry` | `BattingLine` / `BowlingLine` (column names align) |
| `fall_of_wicket` | `FallOfWicket` |
| `partnership` | `Partnership` (wicket_number is the partnership ordinal) |
| `over` | `OverSummary` |
| `player_match_stat` | `PlayerMatchLine` |
| `player_career_stat` | `CareerTotals` |
| `team_stat` / `tournament_standings` | `SeasonTotals` / `StandingsRow` |
| `match_result` | `MatchResult` |
| `dls_target` / `dls_par_score` | `DlsTarget` (never computed) |

The service layer validates rows at the boundary, calls `rebuild_match`, then
persists outputs with `revision`/`computed_at`. `apply_delivery` (incremental
single-delivery application) is deliberately **not** provided: a full innings
rebuild benchmarks at ~3 ms (300-ball ODI) / ~6 ms (600-ball day), so incremental
state would add complexity without measurable benefit.

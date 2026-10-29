# CRICKET_SCORING_RULES.md

Design of the **Cricket Scoring domain service**: algorithms, pseudocode, rule
assumptions and unit tests. Sources of truth: the **Laws of Cricket** (MCC), the
**ICC Playing Conditions** for the relevant format/competition, and the chosen
licensed provider's specification. Statistics conventions follow standard
cricket-scoring definitions (e.g. batting average excludes not-outs; a team
bowled out is charged its full over quota for NRR).

> **Two-tier output.** Every computed value is labelled **official** (a
> normative result — runs, wickets, result, NRR) or **derived/estimated** (a
> convenience metric — dot %, boundary %, projections). Estimates are never
> presented as official figures.

---

## 1. Service boundaries

```
scoring/
  laws/            # pure, dependency-free rule functions (no DB)
  engine.py        # rebuild innings/scorecard from deliveries
  aggregates.py    # career/team/leaderboard aggregates
  standings.py     # points table + NRR
  results.py       # match completion, result, margins, super over
  dls.py           # licensed DLS adapter (NOT reimplemented from scratch)
  config.py        # RuleConfig (format-specific assumptions, versioned)
```

**Principle:** the engine is a **pure function of deliveries + rule config**.
Given the same inputs it always produces the same output, so corrections and
historical rebuilds are reproducible and testable.

### RuleConfig (versioned, per format)

```python
@dataclass(frozen=True)
class RuleConfig:
    version: str                 # e.g. "icc-test-2024"
    balls_per_over: int = 6
    max_innings: int = 2
    overs_limit: int | None = 50
    counts_ball_on_noball: bool = True    # batter faces a ball on a no-ball
    no_ball_byes_off_bat: bool = True     # byes off a no-ball credited as byes
    allows_declaration: bool = False
    allows_follow_on: bool = False
    allows_draw: bool = False
    super_over_enabled: bool = True
    dls_enabled: bool = False
    nrr_full_quota_if_all_out: bool = True
```

---

## 2. Core algorithms

### 2.1 Legal balls, overs and over completion

```
function legal_balls(deliveries): count d where d.is_legal
function ball_in_over(delivery, config):
    # legal-ball counter within the current over, 1-based
    return n_legal_before(delivery) - n_legal_before(start_of_over(delivery)) + 1
function over_is_complete(deliveries_in_over, config):
    return count(legal in over) == config.balls_per_over
function overs_display(legal_balls, config):
    whole = legal_balls // config.balls_per_over
    rem  = legal_balls %  config.balls_per_over
    return f"{whole}.{rem}"
```

**Assumption:** `over_number` and legal-ball counting come from the provider for
live play; for reconstruction we recompute from `is_legal` and never assume
"6 legal balls per over" when the provider records otherwise (e.g. an over
shortened by a declaration or a reduced-overs match). `balls_per_over` is a
config value (default 6), not a hard-coded constant.

### 2.2 Innings totals

```
function innings_totals(deliveries, config):
    runs    = sum(d.total_runs for d in deliveries)          # official
    # `is_wicket` is already false for non-dismissal retirements
    # (RETIRED_HURT / RETIRED_NOT_OUT are non-wicket innings events); every
    # dismissal that counts toward the team tally and RETIRED_OUT has is_wicket=true.
    wickets = count(distinct d.dismissed_player_id
                    for d where d.is_wicket
                      and not is_temporary_runner_case(d))
    legal   = count(d for d if d.is_legal)
    extras  = {
        'wides':     sum(d.wides),
        'noballs':   sum(d.noballs),
        'byes':      sum(d.byes),
        'leg_byes':  sum(d.leg_byes),
        'penalty':   sum(d.penalty_runs),
        'total':     sum(d.wides+d.noballs+d.byes+d.leg_byes+d.penalty_runs),
    }
    return Totals(runs, wickets, legal, extras)
```

**Edge cases:** retired-hurt and retired-not-out are **not** dismissals and do
not count as wickets (they are non-wicket innings events recorded via
`retired_player_id`/`retirement_type`); a batter who retires *out* does, because
`RETIRED_OUT` is a dismissal (`is_wicket=true`). Wicket counting therefore keys
off `is_wicket`, which the schema/scoring service set for every dismissal type
plus `RETIRED_OUT` and never for `HURT`/`NOT_OUT` retirements. Penalty runs never
count as extras to the batter/bowler but do count in the innings total;
temporary runners do not change strike attribution.

### 2.3 Batter runs, balls and the scorecard line

```
function batter_line(player, deliveries, config):
    faced = [d for d in deliveries
             if d.batter_id == player
                and (d.is_legal or (d.is_noball and config.counts_ball_on_noball))]
    runs   = sum(d.runs_off_bat for d in faced)      # wides/noballs BYE runs excluded
    balls  = count(faced)
    fours  = count(d for d in faced if d.runs_off_bat == 4)
    sixes  = count(d for d in faced if d.runs_off_bat == 6)
    dots   = count(d for d in faced if d.runs_off_bat == 0 and d.total_runs == 0)
    ones   = count(d for d in faced if d.runs_off_bat == 1)   # etc. for 2s/3s/5s
    out    = any(d.is_wicket and d.dismissed_player_id == player for d in deliveries)
    sr     = 100 * runs / balls if balls else None
    return BattingLine(runs, balls, fours, sixes, dots, ones, out, sr, ...)
```

**Strike-rate rule:** `balls` counts *legal* balls faced plus (optionally)
no-balls per `counts_ball_on_noball`; wides never count. If `balls == 0`,
`strike_rate = None` (rendered as `"—"`, never `0.00` or a division error).

### 2.4 Bowler runs, wickets, economy

```
function bowler_line(player, deliveries, config):
    legal   = [d for d in deliveries if d.bowler_id == player and d.is_legal]
    charged = sum(d.runs_off_bat + d.wides + d.noballs
                  for d in deliveries if d.bowler_id == player)
    # byes, leg-byes and penalty runs are NOT charged to the bowler
    wickets = count(d for d in deliveries
                    if d.bowler_id == player and d.is_wicket
                       and d.bowler_credited_wicket)
    balls   = count(legal)
    econ    = charged / (balls / config.balls_per_over) if balls else None
    avg     = charged / wickets if wickets else None          # bowling average
    sr      = balls / wickets if wickets else None            # bowling strike rate
    maidens = count(over for over in overs_of(player) if over.runs_conceded == 0
                    and over_is_complete(over))
    return BowlingLine(balls, charged, wickets, maidens, econ, avg, sr)
```

**Denominator-zero rules (explicit):**
- `bowling_avg = runs_conceded / wickets`; if `wickets == 0` → **undefined**
  (`None`), rendered `"—"`. Never `0.00`, never `inf`.
- `bowling_sr = balls / wickets`; if `wickets == 0` → undefined.
- `economy = runs_conceded / overs`; if `balls == 0` → undefined.
- A **run-out** is a wicket for the team but `bowler_credited_wicket=false`, so
  it never enters the bowler's average/strike-rate wicket count.

### 2.5 Batting average (career & innings)

```
function batting_average(runs, innings, not_outs):
    dismissals = innings - not_outs
    return runs / dismissals if dismissals > 0 else None     # None => "—"
```

**Critical rule:** a **not-out** does not count as a dismissal. If a player has
never been dismissed, average is **undefined**, not infinite. The denominator is
`innings − not_outs`, not `innings`. (This is the exact "denominator zero" trap
called out in the brief.)

### 2.6 Dot-ball % and boundary %

```
dot_pct      = 100 * dots / balls_faced            if balls_faced else None
boundary_pct = 100 * (fours + sixes) / balls_faced if balls_faced else None   # derived/estimated
```

These are **derived convenience metrics**, not official statistics.

### 2.7 Partnerships and fall of wickets

A retirement can occur on a ball (kept on the `delivery` row) or with **no ball**
(a rain/drinks/innings break, or before the first delivery) — the latter is an
`InningsEvent` (see the domain model §3.13). Both are exposed through one merged
accessor so the rebuild has a single logical source:

```
function innings_retirements(innings):
    # on-ball retirements (delivery) + off-ball retirements (innings_event)
    from_balls  = [(d.delivery_seq, d.retired_player_id, d.retirement_type)
                   for d in innings.deliveries(is_current only) if d.retired_player_id]
    from_events = [(e.anchor_delivery_seq, e.player_id, kind(e.event_type))
                   for e in innings.events(is_current only)
                   if e.event_type in ('RETIREMENT_HURT','RETIREMENT_NOT_OUT')]
    # (anchor, player, kind in HURT|NOT_OUT); a retirement is NEVER recorded twice.
    # anchor = the delivery immediately AFTER which it takes effect; NULL sorts first.
    return sorted(from_balls + from_events, key=anchor)

function merged_timeline(innings):
    # deliveries and off-ball innings events on one ordered timeline
    return interleave(innings.deliveries(is_current only, by delivery_seq),
                      sorted(innings.events(is_current only), key=anchor_delivery_seq))

function partnerships(innings, config):
    parts, current = [], Partnership(batter1, batter2, runs=0, balls=0)
    for node in merged_timeline(innings):
        if node is a delivery d:
            current.runs += d.total_runs
            if d.is_legal: current.balls += 1
            add boundaries
            if d.is_wicket:                    # dismissal -> FOW + new partnership
                parts.append(current); record_fall_of_wicket(d)
                current = Partnership(surviving_batter, new_batter, ...)
            elif d.retired_player_id:          # on-ball retirement (no FOW)
                parts.append(current)
                current = Partnership(surviving_batter, new_batter, ...)
        elif node is an innings_event e and e.event_type is RETIREMENT_*:
            parts.append(current)              # off-ball retirement (no FOW)
            current = Partnership(surviving_batter, new_batter, ...)
        # RESUME / OTHER events do not break a partnership
    parts.append(current)   # unbroken
```

Fall of wicket: recorded only on `is_wicket` deliveries (including
`RETIRED_OUT`) as `(wicket_number, score_at_dismissal, over_display,
delivery_seq, dismissed_player)`. Retirements (`HURT`/`NOT_OUT`) never create a
fall-of-wicket row.

### 2.8 Run rates, targets, remaining

```
current_run_rate(innings) = runs / (legal_balls / bpo)               if legal_balls else None
required_run_rate(chasing) = (target - runs) / ((total_balls - legal_balls) / bpo)
                             if remaining_balls > 0 else None
runs_required = target - runs
balls_remaining = total_balls - legal_balls
```

`total_balls` derives from `overs_limit` and any reductions (DLS). `None` is
returned when the denominator is zero (e.g. last ball faced) rather than a
division error.

### 2.9 Match completion & result determination

```
function decide_result(match, innings, config):
    # All-out / overs-complete / target-reached / declaration / draw paths
    if match.status in {ABANDONED, CANCELLED}: return NO_RESULT
    if config.allows_draw and time_expired(match) and match.status==COMPLETED:
        return DRAW
    last, first = innings[-1], innings[0]
    if match.is_dls_applied:
        target = last.dls_target
    else:
        target = first.runs + 1
    if last.runs >= target:
        return WIN(winner=last.batting_team, margin=wickets=10-last.wickets)
    if last.all_out or overs_complete(last):
        if last.runs == first.runs: return TIE
        return WIN(winner=first.batting_team, margin=runs=first.runs-last.runs)
    return None  # still in progress
```

**Margin types:** `runs` (defending side wins), `wickets` (chasing side wins),
`innings` (a side wins by an innings and N runs — Test), or `tie`/`draw`/
`no_result`. Forfeits and penalties follow special branches documented in the
result-type enum.

The returned outcome is written to `match.result_type` (and `MatchResult`).
`TIED` and `NO_RESULT` are **outcome** values, never `match_status` values: a
tied match is `status=COMPLETED, result_type=TIE`; a no-result match is
`status=COMPLETED` (or `ABANDONED`) with `result_type=NO_RESULT`.

### 2.10 Career / team aggregates

```
function rebuild_player_career(player, scope, config):
    lines = player_match_stat(player, scope)     # durable per-match facts
    runs       = sum(l.batting.runs)
    innings    = count(l for l in lines if l.did_bat)
    not_outs   = count(l for l in lines if l.did_bat and not l.batting.out)
    high_score = max(l.batting.runs, key=(runs, not_out))    # prefer not-out at equal
    bat_avg    = runs / (innings - not_outs) if (innings-not_outs)>0 else None
    wickets    = sum(l.bowling.wickets)
    five_fers  = count(l for l in lines if l.bowling.wickets >= 5)
    best_bowl  = max over lines by (wickets, -runs)
    ...
```

### 2.11 Tournament standings and Net Run Rate

```
function net_run_rate(team):
    runs_for, balls_faced   = season_totals(team, batting=True)
    runs_against, balls_bowled = season_totals(team, batting=False)
    # Bowled out => use FULL entitled overs, not overs actually batted
    overs_faced   = full_quota_or_actual(balls_faced,   all_out=team_bowled_out)
    overs_bowled  = full_quota_or_actual(balls_bowled,  all_out=opponent_bowled_out)
    return (runs_for/overs_faced) - (runs_against/overs_bowled)
```

**NRR rules (from the brief and the Laws/playing conditions):**
- If a team is **bowled out**, its run-rate uses the **full quota** of overs it
  was entitled to (e.g. 50 or 20), *not* the overs actually faced.
- If a match is **interrupted with a revised DLS target**, NRR uses the revised
  figures consistent with the playing conditions (see the DLS section).
- An **abandoned** match with no result contributes no runs/overs to NRR.
- A **retrospective DLS result** follows the same revised-target convention.
- Standings points follow the competition's `points_system` (default: win 2,
  tie/NR 1, loss 0; some competitions add batting/bowling bonus points — read
  from `series.points_system`).

### 2.12 Super Over statistics

- Super Over is its own `innings` (`is_super_over=true`). Runs/wickets are
  recorded normally but are **excluded from career/Tournament NRR** by default
  (config flag `include_super_over_in_nrr=false`) and reported separately.
- A tied Super Over triggers another Super Over (`super_over_number+1`); the
  tie-break, if any boundary-count rule applies, is recorded in
  `match.result_type` with a documented rule version.

### 2.13 Duckworth–Lewis–Stern (DLS)

**Do not reimplement DLS.** DLS is a proprietary method with licensed
implementations and published tables. **Decision (recommendation):** the platform
**never self-computes DLS**; it stores and displays the target/par score supplied
by the licensed provider, marked `source=provider`. The value is stored in
`innings.dls_target` / `match.dls_par_score` with the **method version** so a
result is reproducible. If a properly licensed DLS engine is later contracted it
may replace the provider value (recorded as `source=licensed`); an unlicensed
self-computation is never permitted.

### 2.14 Non-6-ball formats (e.g. The Hundred)

Some formats do not bowl six legal balls per over. The Hundred bowls **100 balls
per innings as 20 sets of 5** (`format_catalogue.the_hundred.balls_per_over = 5`).
The over/set unit is a **per-format** value already carried by `RuleConfig`
(`balls_per_over`) and `format_catalogue`; nothing else in the model changes.

- **Over/set completion** — `over_is_complete` (§2.1) and the maiden count (§2.4)
  both key off `config.balls_per_over`, so a set is complete after its 5th legal
  ball and a maiden is 5 legal balls with zero runs charged to the bowler. No
  format-specific branch is needed.
- **`overs_display`** — always rendered as `full_sets.remaining_balls` using that
  format's unit: 7 balls into a Hundred innings shows `1.2` (1 completed 5-ball
  set + 2 balls), and a full innings shows `20.0`. The HTML/Ball display is
  computed, never stored.
- **Economy** — `economy = charged / (balls / config.balls_per_over)` (§2.4), i.e.
  **runs per 5-ball set** for The Hundred, per 6-ball over elsewhere. The
  denominator is always `balls / balls_per_over`, so the metric stays comparable
  within a format and is never hard-coded to overs of six.
- **End changes** — in The Hundred the ends change at the start (`0`) and after
  **every 10 balls**, not every 6. **Decision (recommendation):** this is a
  **presentation-only** rule; it affects the displayed "end" indicator and
  commentary but has **no effect** on the schema, strike rotation, or scoring.
  Strike rotation still follows the normal odd-runs / end-of-over rules, so it
  needs no special case. `format_catalogue` may carry the change interval as a
  display hint but no scoring column depends on it.
- **Innings length** — `total_balls = 100` for a full Hundred innings, derived
  from `overs_limit (20) × balls_per_over (5)`; reduced-overs matches recompute
  it the same way (§2.8, E20).

---

## 3. Edge cases and assumptions (explicit)

| # | Case | Rule / handling |
|---|---|---|
| E1 | Batter never dismissed | Batting average = undefined (`—`), not ∞ |
| E2 | Bowler has 0 wickets | Bowling average & SR = undefined |
| E3 | 0 balls faced/bowled | SR / economy = undefined |
| E4 | Wide | Not a legal ball; +1 free run (+ runs run); batter's balls unchanged; bowler charged |
| E5 | No-ball (off bat) | Not legal; batter credited runs; batter faces a ball (config); bowler charged `noballs + runs` |
| E6 | No-ball + byes | Byes credited as byes; bowler charged only the no-ball penalty |
| E7 | Byes / leg-byes | Legal ball; not charged to bowler; count toward innings total |
| E8 | Penalty runs | Awarded to a side; added to innings total; not to batter/bowler |
| E9 | Run-out | Team wicket; `bowler_credited_wicket=false`; fielder ids recorded |
| E10 | Retired hurt / retired not out | **Not a dismissal**; a non-wicket innings event (`retired_player_id` + `retirement_type`); may resume; counts as **not-out** in averages; adds no FOW row |
| E11 | Retired out | A dismissal (`is_wicket=true`, `dismissal_type=RETIRED_OUT`); counts as a **team wicket**; adds a FOW row |
| E12 | Declared innings | `declared=true`; innings ends; NRR uses overs actually faced (not full quota) unless bowled out |
| E13 | Follow-on | `is_follow_on_innings`; standard result logic still applies |
| E14 | Team bowled out (all out) | NRR uses **full entitled overs** |
| E15 | Target reached on the last legal ball | Result computed after applying the delivery; `>` not `>=` around tie boundaries is tested explicitly |
| E16 | Tied match | `result_type='TIE'` |
| E17 | Abandoned / no result | No winner; NRR-contributing runs/overs excluded |
| E18 | DLS revised target | Use revised target; never compute unlicensed DLS |
| E19 | Super Over | Separate innings; excluded from career/NRR by default |
| E20 | Over reduced (rain) | `overs_limit` reduced by playing conditions; `total_balls` updates |
| E21 | Multi-day Test | `source_local_date` distinguishes days; innings spanning days grouped by `innings_number` |
| E22 | Substitute/Impact player | `is_substitute`; not counted as "matches" differently unless the format says so (config) |
| E23 | Partial historical data | Metrics needing balls are `estimated`/absent; never faked |
| E24 | Provider correction mid-innings | Rebuild from corrected deliveries; supersede derived rows by revision |
| E25 | Non-6-ball format (The Hundred) | `balls_per_over=5`: over/set = 5 legal balls; `overs_display`, economy and maidens use the format's unit; the 10-ball end change is presentation-only (§2.14) |

---

## 4. Official vs. estimated classification

| Metric | Class |
|---|---|
| Runs, wickets, balls, extras, partnerships, FOW, result, margins, NRR, points | **Official** (normative) |
| Batting average, bowling average, economy, strike rate, 5-fers, best figures | **Official statistics** (standard definitions) |
| Dot-ball %, boundary %, run-rate projections, "form index" | **Derived / estimated** |
| DLS par score | **Official if provider/licensed supplied**; never self-computed without a licence |

---

## 5. Unit-test examples (critical algorithms)

```python
# --- Batting average: never-dismissed player ---
def test_batting_average_undefined_when_never_out():
    assert batting_average(runs=500, innings=10, not_outs=10) is None

def test_batting_average_excludes_not_outs():
    # 1000 runs, 20 innings, 5 not-outs => 1000/15
    assert batting_average(runs=1000, innings=20, not_outs=5) == pytest.approx(66.666, 0.01)

# --- Bowling average: zero wickets ---
def test_bowling_average_undefined_without_wickets():
    line = compute_bowling_line(deliveries_with_no_wicket)
    assert line.wickets == 0 and line.bowling_avg is None and line.economy is not None

# --- Wides / no-balls accounting ---
def test_wide_does_not_advance_over_or_batter_balls():
    d = Delivery(wides=1, runs_off_bat=0)
    assert d.is_legal is False and d.total_runs == 1
    assert batter_balls(d) == 0            # wide never faces a ball

def test_noball_charges_bowler_and_batter_faces_ball():
    d = Delivery(noballs=1, runs_off_bat=4, counts_ball_on_noball=True)
    assert d.is_legal is False and d.total_runs == 5
    assert bowler_runs_charged(d) == 5     # noball + runs off bat
    assert batter_balls(d) == 1

def test_byes_not_charged_to_bowler_but_count_in_total():
    d = Delivery(byes=2)
    assert d.is_legal is True and d.total_runs == 2
    assert bowler_runs_charged(d) == 0

# --- Over completion from legal balls only ---
def test_over_complete_after_six_legal_balls_even_with_wides():
    balls = [Wide(), Legal(), Legal(), Legal(), Legal(), Legal(), Legal()]
    assert over_is_complete(balls, balls_per_over=6) is True

# --- Non-6-ball format: The Hundred (5-ball sets, 100 balls) ---
HUNDRED = RuleConfig(balls_per_over=5, overs_limit=20)

def test_hundred_set_complete_after_five_legal_balls():
    balls = [Wide(), Legal(), Legal(), Legal(), Legal(), Legal()]
    assert over_is_complete(balls, balls_per_over=5) is True
    assert over_is_complete([Legal()] * 4, balls_per_over=5) is False

def test_overs_display_hundred_uses_five_ball_unit():
    assert overs_display(legal_balls=7, config=HUNDRED) == "1.2"    # 1 set + 2 balls
    assert overs_display(legal_balls=100, config=HUNDRED) == "20.0"

def test_hundred_economy_is_runs_per_five_ball_set():
    # 50 runs charged off 10 legal balls => 10 balls / 5 = 2 sets => 25.00
    line = bowler_line('A', ten_legal_balls_charged(50), HUNDRED)
    assert line.economy == pytest.approx(25.0, 0.01)

def test_hundred_maiden_is_five_legal_balls_with_no_runs():
    ov = [Legal()] * 5                                   # 5 legal, 0 charged
    assert is_maiden(ov, HUNDRED) is True

def test_hundred_innings_is_100_balls():
    inn = innings_with_legal_balls(100)
    assert inn.total_balls == HUNDRED.overs_limit * HUNDRED.balls_per_over  # 100

# --- NRR: bowled out uses full quota ---
def test_nrr_uses_full_quota_when_all_out():
    batting = SeasonTotals(runs_for=200, balls_faced=100, all_out=True)  # 16.4 overs faced
    assert overs_used_for_nrr(batting, overs_limit=50) == 50

# --- Result determination ---
def test_chasing_side_wins_by_wickets():
    first  = Innings(runs=300, wickets=10)
    second = Innings(runs=301, wickets=4)
    res = decide_result(match(format='ODI'), [first, second], RuleConfig(overs_limit=50))
    assert res.type == 'WIN' and res.winner == second.batting_team and res.margin_type == 'wickets'

def test_tie_when_scores_equal_and_overs_complete():
    first, second = Innings(runs=250, wickets=10), Innings(runs=250, wickets=8)
    res = decide_result(match(), [first, second], RuleConfig(overs_limit=50))
    assert res.type == 'TIE'

def test_abandoned_is_no_result():
    assert decide_result(match(status='ABANDONED'), [], RuleConfig()).type == 'NO_RESULT'

# --- Retirements: hurt is not a wicket, out is, and a resuming batter is not out ---
def test_retired_hurt_does_not_increment_wickets():
    balls = [Legal(), RetiredHurt(player='A')]      # is_wicket=False, retirement_type='HURT'
    assert innings_totals(balls, RuleConfig()).wickets == 0

def test_retired_out_increments_wickets():
    balls = [Legal(), RetiredOut(player='A')]       # is_wicket=True, dismissal_type='RETIRED_OUT'
    assert innings_totals(balls, RuleConfig()).wickets == 1

def test_retired_hurt_resumes_and_is_not_out_in_averages():
    # batter retires hurt, later resumes and finishes not out
    line = batter_line('A', innings_with_retirement_return('A'), RuleConfig())
    assert line.out is False                        # not a dismissal
    assert batting_average(runs=line.runs, innings=1, not_outs=1) is None

def test_off_ball_retirement_is_read_by_merged_accessor_once():
    # retirement during a rain break has no delivery; it lives in innings_event
    inn = innings(retirements=[InningsEvent('RETIREMENT_HURT', player='A',
                                            anchor_delivery_seq=None)])
    rets = innings_retirements(inn)
    assert len(rets) == 1 and rets[0].player == 'A'
    assert innings_totals(inn.deliveries, RuleConfig()).wickets == 0  # not a wicket

def test_retirement_never_recorded_twice():
    # a ball retirement AND a matching innings_event must not both exist
    inn = innings(deliveries=[..., RetiredHurt(player='A')],
                  events=[InningsEvent('RETIREMENT_HURT', player='A', anchor=...)])
    with pytest.raises(DataQualityError):
        validate_single_retirement_source(inn)
```

Integration-level lifecycle tests (Test multi-innings, ODI DLS, T20 Super Over)
are in [CRICKET_TESTING_STRATEGY.md](CRICKET_TESTING_STRATEGY.md).

---

## 6. Recalculation pipeline (two tiers)

Rebuilds are split by cost and by trigger. All stages are idempotent and write
`revision`/`computed_at`.

**Tier 1 — on every delivery append or correction.** Cheap, per-innings; keeps
the live scorecard correct within the freshness SLO:

```
deliveries (canonical, is_current only)
  -> innings_summary
  -> batting_entry / bowling_entry
  -> fall_of_wicket / partnership / over
  -> cache layer (Redis) invalidation (match:{id}:*)
  -> realtime event emission (delivery.appended / innings.updated)
```

**Tier 2 — on `match.completed`, `correction.applied`, and scheduled/periodic
jobs.** Expensive, cross-match; never runs on a live delivery:

```
player_match_stat (per completed match)
  -> player_career_stat / team_stat
  -> tournament_standings / leaderboard / head_to_head / form_trend
  -> fantasy player_match_performance (per ruleset version)
  -> cache invalidation (series / leaderboard / player keys)
```

A `recompute(scope)` function can rebuild any subtree (Tier 1 or Tier 2) for
corrections or historical imports. Because a live delivery only touches Tier 1,
career and team tables are never rewritten ball-by-ball.

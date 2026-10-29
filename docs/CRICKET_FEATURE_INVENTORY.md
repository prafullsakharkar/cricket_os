# CRICKET_FEATURE_INVENTORY.md

Feature inventory for Cricket OS, derived from the Phase 2 website analysis.
Columns: **Feature · Observable data · Backend capability · Data freshness ·
Evidence · Confidence**.

**Confidence:** H = observed directly on the public site; M = observed + minor
inference; L = architectural inference (not observed).
**Freshness:** actual/live = seconds; entity = changes on match events; batch =
after-match/periodic.
Evidence references (R-numbers) map to
[CRICKET_PLATFORM_RESEARCH.md](CRICKET_PLATFORM_RESEARCH.md#4-phase-2--research-log).

---

## 1. Verified observations (from public pages)

### 1.1 Live matches & match state

| Feature | Observable data | Backend capability | Data freshness | Evidence | Conf. |
|---|---|---|---|---|---|
| Live match listing | Team pairs, short status (`Lead by 50`, `opt to bowl`, `Tea`, `LIVE`) | `GET /matches/live` with status summary projection | actual (≤5 s) | R6, R7 | H |
| Live / Recent / Upcoming tabs | Three filters of the match list | `state` filter param on matches endpoint | actual | R6 | H |
| Match detail shell | Teams, venue (ground/city/timezone), format, series, start/end, state, status | `GET /matches/{id}` | actual (≤5 s) | R9, R13 | H |
| Match tabs | Info, Live, Scorecard, Squads, Overs, Graphs, Highlights, Commentary, News | Sub-resources on the match resource | mixed | R9 | H |

### 1.2 Scorecard

| Feature | Observable data | Backend capability | Data freshness | Evidence | Conf. |
|---|---|---|---|---|---|
| Batting card | Batsman id/name/shortName, captain/keeper flags, balls, dots, 4s, 6s, mins, SR, outDesc, bowler/fielder ids, run-split (1s/2s/3s/5s), boundaries, sixers, wicketCode, overseas/inXI-change flags | `GET /matches/{id}/scorecard` (derived read model) | actual/after-event | R11 | H |
| Bowling card | (Equivalent structure; bowlers with overs/maidens/runs/wickets/econ) | `GET /matches/{id}/scorecard` | actual | R11, R15 (bowler striker fields) | M |
| Innings summary | Per-innings runs/wickets/overs, `isDeclared`, `isFollowOn` | `inngs` on match summary | actual | R12 | H |
| Fall of wickets / partnerships | Not directly labelled in keys; implied by wicket events per ball | `fall_of_wickets`, `partnerships` read models | after-event | R15 (`event` per ball), **inference** | L |

### 1.3 Ball-by-ball & commentary

| Feature | Observable data | Backend capability | Data freshness | Evidence | Conf. |
|---|---|---|---|---|---|
| Full commentary / ball feed | `commentaryList`: comm text, ball number, event, striker/bowler per ball, legalRuns, totalRuns, team score | `GET /matches/{id}/commentary` (licensed) | actual (≤5 s) | R14, R15 | H |
| Over-by-over | Over summary list | `GET /matches/{id}/overs` | actual | R9, R15 (`overSummaryList`) | H |
| Per-player match performance | `.../player/{pid}/{batting|bowling}` | `GET /matches/{id}/players/{pid}/performance` | after-event | R16 | H |

### 1.4 Series / tournaments

| Feature | Observable data | Backend capability | Data freshness | Evidence | Conf. |
|---|---|---|---|---|---|
| Series home/tabs | Home, Matches, Table, Stats, Squads, Venues | Sub-resources on series | mixed | R17 | H |
| Points table | team, played/won/lost/tied/drawn/noRes, NRR, points, form, qualifyStatus, group, legends, rules | `GET /series/{id}/points-table` (derived) | ≈22 s | R18, R19 | H |
| Series statistics | Headers+values rows, format filter, stat types/categories | `GET /series/{id}/stats?type=` | ≈1 s | R20 | H |
| Archives by year | International/Domestic/League/Women series maps by year | `GET /archives`, `GET /series?year=&category=` | batch (≤5 s) | R21 | H |

### 1.5 Teams & players

| Feature | Observable data | Backend capability | Data freshness | Evidence | Conf. |
|---|---|---|---|---|---|
| Team directory | Category tabs (Domestic/League/Women); team slug+id | `GET /teams?category=` | batch | R22 | H |
| Squad | Per team: players with role, captain, keeper, substitute, batting/bowling style, overseas, in-match & playing-XI change | `GET /matches/{id}/squads`, `GET /teams/{id}/squads` | entity (≤5 s) | R29 | H |
| Player profile | fullName, height, role, intlTeam, teams, DoB, bio, career debut/lastPlayed, batting/bowling stats, recent form, rankings | `GET /players/{id}`, `/stats`, `/form` | ≤5 s | R23, R24 | H |
| Player match history | all-matches batting/bowling | `GET /players/{id}/matches` | after-match | R25 | H |
| Career batting/bowling stats | Headers+values by format, series spinner | `GET /players/{id}/stats?format=` | batch | R24 | H |

### 1.6 Rankings & schedule

| Feature | Observable data | Backend capability | Data freshness | Evidence | Conf. |
|---|---|---|---|---|---|
| ICC rankings | Per gender/format: rank, rating, points, trend | `GET /rankings?gender=&format=&category=` | ≤5 s | R26, R27 | H |
| Schedule | Matches grouped by date; international/league/domestic/women/all | `GET /matches/upcoming?from=&to=&series_type=` | ≈26 s | R28 | H |

---

## 2. Architectural inferences (not directly observed)

| Feature | Observable data (inferred) | Backend capability | Freshness | Evidence | Conf. |
|---|---|---|---|---|---|
| Real-time push | Page updates without reload | WebSocket/SSE `/ws/matches/{id}` or `/sse/matches/{id}` | actual | Client transport not observed | L |
| Search | None observed in nav | `GET /search?q=` across players/teams/series | near-real-time (index) | Not observed | L |
| Historical records pages | None observed | `GET /records/...` aggregations | batch | Not observed | L |
| Head-to-head | None observed | `GET /head-to-head?...` | batch | Not observed | L |
| Fantasy points | None observed on site | `GET /fantasy/points?...` | after-match | Out of Cricbuzz scope | L |
| Player comparison | None observed | `GET /players/compare?...` | batch | Not observed | L |
| Corrections/reconciliation | Not visible in UI | Admin + ingestion reconciliation | async | Internal | L |
| Missing-data / error states | Generic (not enumerated) | 404/`state=unavailable` conventions | — | Not enumerated | L |

---

## 3. Cross-cutting findings that shape the backend

1. **Entity identity is external-data-driven.** Player, team, series, match all
   have provider IDs (`batId`, `teamId`, `seriesId`, `matchId`). The platform
   must store **internal UUIDs + provider IDs** separately.
2. **Versioned participation.** `playingXIChange`, `inMatchChange`,
   `isOverseas`, `captain`, `keeper` → version the playing XI per match/day.
3. **Derived vs. supplied.** Scores, NRR, points and stats appear *calculated*;
   match facts, squads, rankings appear *provider-supplied*. The schema must
   mark provenance.
4. **Commentary is licensed content.** It may be excluded or gated; it is not a
   freely redistributable field.
5. **Freshness tiers.** Live (≤5 s) vs. table/schedule (≈20–30 s) vs. archival
   (minutes/days) — drives caching and ingestion cadence.

---

## 4. In-scope vs. out-of-scope (per project brief)

**In scope:** live scores, ball-by-ball, innings progression, batting/bowling
scorecards, fall of wickets, partnerships, licensed commentary, fixtures,
results, international + domestic + IPL/T20 leagues, Test/ODI/T20I, teams,
squads, players, profiles, career stats, rankings, leaderboards, series
standings, points tables, head-to-head, records, form/trends, optional fantasy
points, search/filtering, historical ingestion/sync, real-time delivery.

**Out of scope:** news/editorial, blogs, social, betting/gambling, payments,
paid fantasy contests, CRM/org-management, unrelated app features. (Cricbuzz's
News/Videos/Photos tabs are explicitly **not** modelled.)

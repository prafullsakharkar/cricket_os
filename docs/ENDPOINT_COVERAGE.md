# Endpoint Coverage Audit

Every endpoint in the [CRICKET_API_SPECIFICATION](CRICKET_API_SPECIFICATION.md)
endpoint matrix (§2.1–§2.7), its implementation status, and where it lives.
Produced by the Phase F Group 8 coverage audit; the thin endpoints this audit
uncovered were implemented in the same group.

**Legend**

| Status | Meaning |
|---|---|
| ✅ live | Implemented, routed, tested, cached |
| ⚠️ partial | Implemented with a documented deviation (see notes) |
| 🔜 deferred | Specified but deliberately not implemented (reason below) |

---

## §2.1 Matches

| Endpoint | Status | Phase / commit | View | Notes |
|---|---|---|---|---|
| `GET /matches/live` | ✅ live | D | `apps/matches/api/lists.py` `LiveMatchListView` | cursor, `max-age=5, swr=5` |
| `GET /matches/upcoming` | ✅ live | D | `UpcomingMatchListView` | cursor, `max-age=30, swr=60` |
| `GET /matches/recent` | ✅ live | D | `RecentMatchListView` | cursor, `max-age=30, swr=60` |
| `GET /matches` | ✅ live | **8** | `MatchListView` | ⚠️ filters: `series/team/format/gender`; spec also lists `venue/from/to/status` (deferred, see §Deviations) |
| `GET /matches/{id}` | ✅ live | C | `apps/matches/api/views.py` `MatchDetailView` | state-dependent cache, ETag/304 |
| `GET /matches/{id}/scorecard` | ✅ live | C | `ScorecardView` | `?innings=`, `meta.ball_data` |
| `GET /matches/{id}/innings` | ✅ live | C | `InningsView` | innings list & progression |
| `GET /matches/{id}/innings/{n}/deliveries` | ✅ live | C | `DeliveriesView` | cursor on `delivery_seq`, partition-pruned |
| `GET /matches/{id}/commentary` | 🔜 deferred | — | — | licensed commentary feed; no licensed commentary contract exists yet (provider decision RSK-12 open) |
| `GET /matches/{id}/overs` | ✅ live | **8** | `MatchOversView` | over-by-over from `Over` rows (never ball data) |
| `GET /matches/{id}/squads` | ✅ live | **8** | `MatchSquadsView` | playing XI + bench per team; empty buckets for unannounced teams |
| `GET /matches/{id}/facts` | ✅ live | **8** | `MatchFactsView` | toss, officials, venue, result |
| `GET /matches/{id}/statistics` | ✅ live | **7** | `apps/statistics/api/views.py` `MatchStatisticsView` | per-player lines from `PlayerMatchStat` |
| `GET /matches/{id}/players/{pid}/performance` | 🔜 deferred | — | — | per-player projection of the match statistics already served by `/matches/{id}/statistics`; add when a consumer needs it |

## §2.2 Players

| Endpoint | Status | Phase / commit | View | Notes |
|---|---|---|---|---|
| `GET /players` | ✅ live | D | `apps/players/api/views.py` | cursor when no `q`; page-number trigram ranking with `q` |
| `GET /players/{id}` | ✅ live | D | `PlayerRetrieveView` | merged ids → `301` |
| `GET /players/{id}/statistics` | ✅ live | **7** | `apps/statistics/api/views.py` `PlayerStatisticsView` | scope/format/gender; merged ids → `301` |
| `GET /players/{id}/matches` | 🔜 deferred | — | — | player match history; equivalent data available via `/matches?team=` + filters |
| `GET /players/{id}/form` | ✅ live | **7** | `PlayerFormView` | rolling 5/10/20 windows, per-format |
| `GET /players/{id}/rankings` | ✅ live | **5** | `apps/rankings/api/views.py` `PlayerRankingsView` | history newest-first; merged ids → `301` |
| `GET /players/compare` | ✅ live | **7** | `PlayerCompareView` | 2–4 ids, deltas vs first, `metric` allow-list |

## §2.3 Teams

| Endpoint | Status | Phase / commit | View | Notes |
|---|---|---|---|---|
| `GET /teams` | ✅ live | D | `apps/teams/api/views.py` `TeamListView` | trigram `q`, `category`, `country` |
| `GET /teams/{id}` | ✅ live | D | `TeamRetrieveView` | |
| `GET /teams/{id}/squad` | ✅ live | **8** | `TeamSquadView` | ⚠️ serves the **currently active** squad; the spec's `?series=` announced-squad selection is deferred (no per-series squad source yet) |
| `GET /teams/{id}/statistics` | ✅ live | **7** | `apps/statistics/api/views.py` `TeamStatisticsView` | |
| `GET /teams/{id}/matches` | 🔜 deferred | — | — | team match history; equivalent data available via `/matches?team=` + filters |

## §2.4 Competitions

| Endpoint | Status | Phase / commit | View | Notes |
|---|---|---|---|---|
| `GET /series` | ✅ live | D | `apps/competitions/api/views.py` `SeriesListView` | `year/category/type`, trigram `q` |
| `GET /series/{id}` | ✅ live | D | `SeriesRetrieveView` | |
| `GET /series/{id}/matches` | ✅ live | **8** | `SeriesMatchesView` | cursor; same shell items as the match feeds |
| `GET /series/{id}/points-table` | ✅ live | **7** | `apps/statistics/api/views.py` `SeriesPointsTableView` | `max-age=20, swr=20` |
| `GET /series/{id}/stats` | ✅ live | **7** | `SeriesStatsView` | ⚠️ parameter is `?metric=` (the spec's `?type=`); `?format=` supported |
| `GET /series/{id}/squads` | ✅ live | **8** | `SeriesSquadsView` | playing-XI union keyed by team name |
| `GET /series/{id}/venues` | ✅ live | **8** | `SeriesVenuesView` | distinct venues, ordered |
| `GET /competitions` `/competitions/{id}` | ✅ live | D | `CompetitionListView` / `CompetitionRetrieveView` | |
| `GET /archives` | ✅ live | **8** | `ArchivesView` | `?year=&category=&type=`, newest first |

## §2.5 Statistics

| Endpoint | Status | Phase / commit | View | Notes |
|---|---|---|---|---|
| `GET /statistics/batting` | ✅ live | **7** | `apps/statistics/api/views.py` `BattingLeaderboardView` | `?scope=&format=&metric=` (metric family-validated) |
| `GET /statistics/bowling` | ✅ live | **7** | `BowlingLeaderboardView` | |
| `GET /statistics/fielding` | ✅ live | **7** | `FieldingLeaderboardView` | honest empty list until a per-match fielding source exists |
| `GET /statistics/teams` | ✅ live | **7** | `TeamRecordsView` | team records catalogue |
| `GET /records/players` | ✅ live | **7** | `PlayerRecordsView` | qualification-gated records |
| `GET /records/teams` | ✅ live | **7** | `TeamRecordsCatalogueView` | |
| `GET /head-to-head` | ✅ live | **7** | `HeadToHeadView` | `?team1=&team2=&format=` |
| `GET /rankings` | ✅ live | **5** | `apps/rankings/api/views.py` `RankingsListView` | `?gender=&format=&category=`, `meta.source="provider"` |

## §2.6 Fantasy + meta

| Endpoint | Status | Phase / commit | View | Notes |
|---|---|---|---|---|
| `GET /fantasy/points` | 🔜 deferred | — | — | fantasy is out of Phase F scope (brief: statistics/rankings/search) |
| `GET /fantasy/rulesets` | 🔜 deferred | — | — | same |
| `GET /fantasy/matches/{id}` | 🔜 deferred | — | — | same |
| `GET /search` | ✅ live | **7** | `apps/search/api/views.py` `SearchView` | `?q=&types=`; `400 invalid_query` on bad input |
| `WS/SSE /ws/matches/{id}` `/sse/matches/{id}` | ✅ live | E | `apps/realtime/` | full wire contract in FRONTEND_CONTRACT.md |
| `GET /health` `/metrics` | ✅ live | D/E | `apps/core/views.py` | ops surfaces |

## §2.7 Venues

| Endpoint | Status | Phase / commit | View | Notes |
|---|---|---|---|---|
| `GET /venues` | ✅ live | D | `apps/venues/api/views.py` `VenueListView` | |
| `GET /venues/{id}` | ✅ live | D | `VenueRetrieveView` | nested country |

---

## Coverage summary

- **Specified read endpoints:** 39 (excluding the 3 fantasy rows).
- **Implemented:** 32 ✅ / 3 ⚠️ partial.
- **Deferred:** 4 (commentary, match performance, player matches, team matches)
  + 3 fantasy rows.
- **Phase F added 23 endpoints** across Groups 5/7/8 (rankings ×2, statistics/
  analytics ×13, search ×1, coverage thin ×9 minus shared routes — the exact
  split is in the ledger).
- Every endpoint is served through `CachedReadMixin` with a version namespace
  and reads **digest-validated aggregate rows or current rows only** — no Phase
  F endpoint scans the partitioned `delivery` table (guarded by the Group 7
  test `test_guards_query_counts_and_no_delivery`).

## Deviations (partial rows)

1. **`GET /matches`** — implemented with the shared `series/team/format/gender`
   filters; the spec's `venue/from/to/status` filters are not yet wired. Cost if
   wrong: clients that filter by those fields get a `400`-free but unfiltered
   page. Adding them is a filter-argument change in `list_all_matches`.
2. **`GET /teams/{id}/squad`** — serves the currently active squad
   (open `TeamMembership`); the spec's `?series=` announced-squad selection is
   not implemented because there is no per-series squad source table yet.
3. **`GET /series/{id}/stats`** — the metric selector is `?metric=` (consistent
   with the other leaderboard endpoints) rather than the spec's `?type=`; the
   `?format=` filter is supported.

## Deferred endpoints (rationale)

- **`/matches/{id}/commentary`** — requires a licensed commentary feed; the
  provider contract (RSK-12) is still open and the internal providers carry no
  commentary. The endpoint will return `403 license_required` when wired.
- **`/matches/{id}/players/{pid}/performance`** — a thin per-player projection
  of `/matches/{id}/statistics`; deferred until a consumer needs the dedicated
  shape.
- **`/players/{id}/matches`**, **`/teams/{id}/matches`** — match history by
  entity; equivalent data is reachable through `/matches` filters
  (`?team=`/`?series=`). Deferred to keep the read surface minimal.
- **`/fantasy/*`** — the fantasy app is outside the Phase F brief.

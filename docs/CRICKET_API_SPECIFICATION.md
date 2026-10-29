# CRICKET_API_SPECIFICATION.md

Versioned REST API under `/api/v1/`. Read-only public surface; writes are
restricted to authenticated admins. Timestamps are **UTC ISO-8601** (`Z`);
responses also carry the source local date/time and timezone.

---

## 1. Conventions

| Aspect | Convention |
|---|---|
| Base path | `/api/v1/` |
| IDs | Internal UUIDs in paths (`/matches/{uuid}`); provider IDs never exposed as the primary handle, but a `?provider_id=` filter is available to admins |
| Content type | `application/json` |
| Timestamps | `2026-10-27T08:00:00Z` (UTC) + `source: {local_date, local_time, timezone}` |
| Envelope | `{"data": …, "meta": {...}}` for collections; `{"data": {...}}` for items |
| Pagination | **Cursor** (`?cursor=&limit=`, default 25, max 100) for feeds; **page-number** (`?page=&page_size=`) for offset-friendly tables |
| Sorting | `?sort=field,-field2` (allow-listed per endpoint) |
| Filtering | Explicit query params (`?format=ODI&status=completed`) |
| Match status vs. result | `status` is the match **lifecycle** (`SCHEDULED`…`COMPLETED`/`ABANDONED`/`CANCELLED`/`FORFEITED`/`POSTPONED`); the **outcome** is carried by `result_type` (`WIN`/`TIE`/`DRAW`/`NO_RESULT`/`ABANDONED`/`CANCELLED`/`FORFEIT`/`SUPER_OVER_TIE_BREAK`). `TIED` and `NO_RESULT` are never `status` values — a tied match is `status=COMPLETED, result_type=TIE`. |
| Sparse fields | `?fields=id,status,score` |
| Errors | RFC 7807-style: `{"error":{"code","message","details"}}` |
| Auth | Public read endpoints need no auth; admin endpoints use JWT/OAuth2 + RBAC |
| Rate limiting | Per-IP + per-key token bucket; `429` with `Retry-After` |
| Caching | **State-dependent TTL** (see §2.1 / §3.9): live match sub-resources `max-age=5, stale-while-revalidate=5`; completed `max-age=300, s-maxage=3600` + strong `ETag`; `immutable` only after a configurable post-completion reconciliation window (~24 h). `304` supported |
| Versioning | Path version `/v1/`; additive changes preferred; deprecation headers `Sunset` |
| Conditional serving of licensed data | Commentary/ball fields return `403 license_required` when rights are absent |

### Error catalog

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `invalid_query` | Bad/unknown parameter |
| 401 | `unauthenticated` | Admin-only endpoint |
| 403 | `forbidden` / `license_required` | RBAC / unlicensed content |
| 404 | `not_found` | Unknown id or slug |
| 409 | `conflict` | Idempotency/duplicate write |
| 422 | `unprocessable` | Validation failure |
| 429 | `rate_limited` | Throttled (`Retry-After`) |
| 503 | `upstream_unavailable` | Provider outage with no cached data |

---

## 2. Endpoint matrix

### 2.1 Matches

| Method | Path | Purpose | Cache | Auth |
|---|---|---|---|---|
| GET | `/matches/live` | Currently live matches | `max-age=5, swr=5` | public |
| GET | `/matches/upcoming` | Fixtures in a window | `max-age=30, swr=60` | public |
| GET | `/matches/recent` | Recently finished | `max-age=30, swr=60` | public |
| GET | `/matches` | Search/list with filters (`series,team,format,venue,from,to,status`) | `max-age=30` | public |
| GET | `/matches/{id}` | Match shell + state | `max-age=5, swr=5` | public |
| GET | `/matches/{id}/scorecard` | Batting/bowling cards + extras + FOW | `max-age=5, swr=5` | public |
| GET | `/matches/{id}/innings` | Innings list & progression | `max-age=5` | public |
| GET | `/matches/{id}/innings/{n}/deliveries` | Ball-by-ball | `max-age=5` (cursor) | public |
| GET | `/matches/{id}/commentary` | Licensed commentary | `max-age=5`; `403` if unlicensed | public |
| GET | `/matches/{id}/overs` | Over-by-over summary | `max-age=5` | public |
| GET | `/matches/{id}/squads` | Playing XI + bench | `max-age=10` | public |
| GET | `/matches/{id}/facts` | Match facts/officials/toss | `max-age=30` | public |
| GET | `/matches/{id}/statistics` | Match-level leaderboards | `max-age=15` | public |
| GET | `/matches/{id}/players/{pid}/performance` | Per-player match performance | `max-age=15` | public |

> **State-dependent caching for match sub-resources.** The `Cache` column above
> is the **live** value. Once a match is `COMPLETED` the same resources switch to
> `max-age=300, s-maxage=3600` with a strong `ETag`; they become `immutable` only
> after a configurable **post-completion reconciliation window** (~24 h). A
> correction purges by entity tag and changes the `ETag`. Full policy in §3.9.

### 2.2 Players

| Method | Path | Purpose | Cache |
|---|---|---|---|
| GET | `/players` | Search/list (`q,team,country,role,format`) | `max-age=300` |
| GET | `/players/{id}` | Profile | `max-age=300, swr=600` |
| GET | `/players/{id}/statistics` | Career/format aggregates (`?scope=&format=&gender=`) | `max-age=300` |
| GET | `/players/{id}/matches` | Match history (cursor) | `max-age=120` |
| GET | `/players/{id}/form` | Recent form windows | `max-age=120` |
| GET | `/players/{id}/rankings` | Ranking history | `max-age=300` |
| GET | `/players/compare` | Compare 2–4 players (`?ids=&format=&metric=`) | `max-age=300` |

### 2.3 Teams

| Method | Path | Purpose | Cache |
|---|---|---|---|
| GET | `/teams` | List (`q,category,country`) | `max-age=600` |
| GET | `/teams/{id}` | Team detail | `max-age=600` |
| GET | `/teams/{id}/squad` | Current/announced squad (`?series=`) | `max-age=120` |
| GET | `/teams/{id}/statistics` | Team aggregates | `max-age=300` |
| GET | `/teams/{id}/matches` | Match history (cursor) | `max-age=120` |

### 2.4 Competitions

| Method | Path | Purpose | Cache |
|---|---|---|---|
| GET | `/series` | List (`year,category,type`) | `max-age=600` |
| GET | `/series/{id}` | Series detail | `max-age=60` |
| GET | `/series/{id}/matches` | Fixtures & results | `max-age=30` |
| GET | `/series/{id}/points-table` | Standings | `max-age=20, swr=20` |
| GET | `/series/{id}/stats` | Series leaderboards (`?type=&format=`) | `max-age=20` |
| GET | `/series/{id}/squads` | Series squads | `max-age=120` |
| GET | `/series/{id}/venues` | Venues | `max-age=600` |
| GET | `/competitions` `/competitions/{id}` | Competition + seasons | `max-age=600` |
| GET | `/archives` | Series by year/category | `max-age=300` |

### 2.5 Statistics

| Method | Path | Purpose | Cache |
|---|---|---|---|
| GET | `/statistics/batting` | Batting leaderboard (`?scope=&format=&metric=`) | `max-age=60` |
| GET | `/statistics/bowling` | Bowling leaderboard | `max-age=60` |
| GET | `/statistics/fielding` | Fielding leaderboard | `max-age=60` |
| GET | `/statistics/teams` | Team records | `max-age=60` |
| GET | `/records/players` | Player records | `max-age=300` |
| GET | `/records/teams` | Team records | `max-age=300` |
| GET | `/head-to-head` | H2H (`?team1=&team2=&format=`) | `max-age=300` |
| GET | `/rankings` | ICC rankings (`?gender=&format=&category=`) | `max-age=60` |

### 2.6 Fantasy + meta

| Method | Path | Purpose | Cache |
|---|---|---|---|
| GET | `/fantasy/points` | Points by match/player (`?match=&player=&ruleset_version=`) | `max-age=120` |
| GET | `/fantasy/rulesets` | Available rulesets/versions | `max-age=3600` |
| GET | `/fantasy/matches/{id}` | Points for a match | `max-age=120` |
| GET | `/search` | Cross-entity search (`?q=&types=`) | `max-age=30` |
| WS/SSE | `/ws/matches/{id}` · `/sse/matches/{id}` | Live updates | n/a |
| GET | `/health` `/metrics` | Ops | no-cache |

---

## 3. Detailed endpoint specifications

### 3.1 `GET /api/v1/matches/live`

- **Query:** `format`, `series`, `gender`, `team`, `limit` (default 50).
- **Pagination:** cursor.
- **Sorting:** by `start_time_utc` desc (fixed).
- **Response schema:** array of match summaries (see §4.1).
- **Cache:** `Cache-Control: public, max-age=5, stale-while-revalidate=5`;
  `ETag` on the payload; `304` when unchanged.
- **Auth:** none. **Rate limit:** 60 req/min/IP (burst 120).
- **Query complexity:** single indexed scan on `(status, start_time_utc)` +
  LATERAL join to latest innings summary → O(live matches).

### 3.2 `GET /api/v1/matches/{id}/scorecard`

- **Query:** `innings` (optional), `fields` (sparse).
- **Response:** innings-indexed cards (§4.2). If ball data is absent, only
  `batting`, `bowling`, `extras`, `totals` are returned and `meta.ball_data=false`.
- **Cache:** live `max-age=5, swr=5`; completed `max-age=300, s-maxage=3600`
  (+`immutable` after the ~24 h reconciliation window), strong `ETag` (digest of
  the revision set). A correction changes the `ETag`. See §3.9.
- **Auth:** none. **Errors:** `404` unknown match; `200` with
  `meta.state="rain_delay"` and partial data when live and paused.
- **Retirements:** the innings payload exposes a single merged `retirements[]`
  list (hurt/not-out only; `RETIRED_OUT` appears instead as a dismissal) built
  from ball-anchored retirements on `delivery` **and** off-ball retirements in
  `innings_event`. Each item carries `type`, an `anchor` (the delivery it follows,
  or `null` when before the first ball) and `resumed`, so clients never need to
  query the two sources separately.
- **Complexity:** 2 indexed reads per innings (`batting_entry`, `bowling_entry`)
  + `fall_of_wicket` + `innings_event` (indexed on `innings_id`).

### 3.3 `GET /api/v1/matches/{id}/innings/{n}/deliveries`

- **Query:** `from_seq`, `to_seq`, `over`, `cursor`, `limit` (default 50).
- **Pagination:** cursor on `delivery_seq`.
- **Cache:** live `max-age=5`; completed `max-age=300, s-maxage=3600` (see §3.9).
- **Licensing:** ball-level data always available; `commentary` field only when
  licensed, else omitted and `meta.commentary=false`.
- **Complexity:** partition-pruned indexed range scan on
  `(innings_id, delivery_seq)`.

### 3.4 `GET /api/v1/matches/{id}/commentary`

- **Behavior:** returns commentary lines (licensed). If the deployment lacks
  commentary rights → `403 {"error":{"code":"license_required"}}`.
- **Cache:** `max-age=5`. **Pagination:** cursor on `delivery_seq`.

### 3.5 `GET /api/v1/players/{id}/statistics`

- **Query:** `scope` (`career|format|season|series|team|venue|opposition`,
  default `career`), `format` (`TEST|ODI|T20I|…`), `gender`, `group`
  (`batting|bowling|fielding`).
- **Response:** grouped official statistics plus `derived` block (§4.3).
- **Cache:** `max-age=300, swr=600`.
- **Complexity:** single indexed read on `(player_id, scope_type, format_type)`.

### 3.6 `GET /api/v1/series/{id}/points-table`

- **Query:** `stage`, `group`.
- **Response:** standings rows (§4.4 style) + `legends` + `rules`.
- **Cache:** `max-age=20, swr=20`.
- **Complexity:** indexed read on `(series_id, stage_id)` ordered by `rank`.

### 3.7 `GET /api/v1/rankings`

- **Query:** `gender` (default `M`), `format` (default `TEST`), `category`
  (default `BATTING`), `limit`.
- **Response:** rows `{rank, player, team, rating, points, trend, best}`.
- **Cache:** `max-age=60`. **Source:** provider-supplied (marked
  `meta.source="provider"`).

### 3.8 Real-time `WS /api/v1/ws/matches/{id}`

- **Protocol:** client subscribes; server pushes events:
  `delivery.appended`, `innings.updated`, `match.state_changed`,
  `match.completed`, `correction.applied`.
- **`seq`:** every event carries a **monotonic per-match** sequence
  (`match_event_log.seq`), distinct from the per-innings `delivery_seq`.
- **Recovery:** on reconnect the client sends `last_seq`; the server replays the
  retained `match_event_log` rows for the match. Events are retained for a
  configurable window after completion (default 24 h); if `last_seq` predates the
  retained range the server returns **`resync_required`**, prompting a REST
  refetch of the current state.
- **SSE alternative:** `/sse/matches/{id}` for one-way scoreboards.

---

### 3.9 Caching policy (state-dependent)

Cache lifetime depends on **match state**, resolving the tension between
seconds-level live freshness and long-lived completed content:

| Resource / state | `Cache-Control` | Validators | Notes |
|---|---|---|---|
| Live match sub-resources (`/matches/{id}`, `/scorecard`, `/innings`, `/deliveries`, `/overs`, `/commentary`) | `public, max-age=5, stale-while-revalidate=5` | weak `ETag` on the live payload | seconds-level freshness |
| Completed match sub-resources | `public, max-age=300, s-maxage=3600` | **strong** `ETag` (digest of the revision set) | shared caches hold 1 h; clients revalidate within 5 min |
| Completed, after the reconciliation window (configurable, default 24 h) | `public, max-age=300, s-maxage=31536000, immutable` | strong `ETag` | corrections should have landed |
| Match list (`/matches/live`, `/upcoming`, `/recent`, `/matches`) | state-dependent: live `max-age=5`, otherwise `max-age=30` | `ETag` | a list mixes states |

- A **correction** (`correction.applied`) purges by entity tag and **changes the
  `ETag`**, so caches and clients pick up the new revision even after completion.
- `immutable` is applied only after a configurable post-completion reconciliation
  window (~24 h), because provider corrections can still arrive.

---

## 4. Representative JSON responses

### 4.1 Live matches

```json
{
  "data": [
    {
      "id": "018b1f2c-9a34-7c11-9d2e-1f4a5b6c7d80",
      "format": "TEST",
      "series": { "id": "018b…", "name": "Australia tour of South Africa, 2026" },
      "match_desc": "3rd Test",
      "status": "IN_PROGRESS",
      "status_detail": "AUS lead by 50",
      "state_title": "Day 4 - Tea",
      "teams": [
        { "team": { "id": "…", "name": "South Africa", "abbreviation": "RSA" }, "score": { "runs": 329, "wickets": 10, "overs": "95.4" } },
        { "team": { "id": "…", "name": "Australia", "abbreviation": "AUS" }, "score": { "runs": 379, "wickets": 6, "overs": "102.4" }, "is_batting": true }
      ],
      "venue": { "name": "Newlands", "city": "Cape Town", "timezone": "Africa/Johannesburg" },
      "start_time": "2026-10-27T08:00:00Z",
      "source": { "local_date": "2026-10-27", "timezone": "Africa/Johannesburg" },
      "updated_at": "2026-10-29T09:41:12Z"
    }
  ],
  "meta": { "count": 22, "etag": "W/\"live-8f2\"", "generated_at": "2026-10-29T09:41:13Z" }
}
```

### 4.2 Scorecard (excerpt)

```json
{
  "data": {
    "match_id": "018b…",
    "meta": { "ball_data": true, "commentary": false, "revision": 41 },
    "innings": [
      {
        "innings_number": 1,
        "batting_team": { "id": "…", "name": "South Africa" },
        "totals": { "runs": 329, "wickets": 10, "overs": "95.4", "extras": { "wides": 6, "noballs": 3, "byes": 4, "leg_byes": 5, "penalty": 0, "total": 18 } },
        "batting": [
          { "position": 1, "player": { "id": "…", "name": "Aiden Markram" }, "runs": 41, "balls": 78, "fours": 6, "sixes": 0, "dots": 51, "strike_rate": 52.56, "is_out": true, "dismissal": { "type": "CAUGHT", "bowler": { "id": "…", "name": "Pat Cummins" }, "fielders": [{ "id": "…", "name": "Steve Smith" }] } }
        ],
        "bowling": [
          { "player": { "id": "…", "name": "Pat Cummins" }, "overs": "20.0", "maidens": 5, "runs_conceded": 70, "wickets": 3, "economy": 3.5, "dots": 78, "bowling_average": 23.33 }
        ],
        "fall_of_wickets": [
          { "wicket": 1, "score": 22, "over": "8.3", "player": { "id": "…", "name": "Aiden Markram" } }
        ],
        "partnerships": [
          { "wicket": 0, "runs": 22, "balls": 51, "batters": ["…", "…"], "unbroken": false }
        ],
        "retirements": [
          { "player": { "id": "…", "name": "Aiden Markram" }, "type": "HURT", "anchor": { "delivery_seq": 51, "over": "8.3" }, "resumed": true }
        ]
      }
    ]
  },
  "meta": { "generated_at": "2026-10-29T09:41:13Z" }
}
```

### 4.3 Player statistics

```json
{
  "data": {
    "player": { "id": "…", "name": "Pat Cummins", "role": "BOWLER", "batting_style": "RIGHT_HAND", "bowling_style": "RIGHT_ARM_FAST" },
    "scope": "format",
    "format": "TEST",
    "official": {
      "batting": { "matches": 68, "innings": 91, "runs": 1209, "not_outs": 14, "high_score": 63, "average": 15.70, "strike_rate": 45.1, "hundreds": 0, "fifties": 3 },
      "bowling": { "balls": 14820, "runs_conceded": 6510, "wickets": 294, "maidens": 640, "average": 22.14, "economy": 2.63, "strike_rate": 50.4, "five_wicket_hauls": 12, "best": { "wickets": 6, "runs": 23 } },
      "fielding": { "catches": 34, "stumpings": 0 }
    },
    "derived": { "dot_ball_pct": 61.2, "boundary_pct": 8.4, "label": "derived" }
  },
  "meta": { "official_source": "computed", "as_of": "2026-10-29" }
}
```

### 4.4 Fixtures

```json
{
  "data": [
    {
      "id": "…",
      "series": { "id": "…", "name": "Pakistan ODI Tri-Series, 2026" },
      "format": "ODI",
      "match_desc": "6th Match",
      "status": "SCHEDULED",
      "start_time": "2026-11-02T09:00:00Z",
      "source": { "local_date": "2026-11-02", "local_time": "14:00", "timezone": "Asia/Karachi" },
      "teams": [
        { "team": { "name": "England", "abbreviation": "ENG" } },
        { "team": { "name": "Sri Lanka", "abbreviation": "SL" } }
      ],
      "venue": { "name": "National Stadium", "city": "Karachi", "timezone": "Asia/Karachi" }
    }
  ],
  "meta": { "count": 1, "next_cursor": null }
}
```

### 4.5 Error

```json
{ "error": { "code": "license_required", "message": "Commentary is not available under the current licence.", "details": { "field": "commentary" } } }
```

---

## 5. Pagination, filtering, sorting reference

- **Cursor pagination** for time-ordered feeds (`live`, `deliveries`,
  `commentary`, `matches/{id}/matches`): cursor encodes
  `(sort_key, id)`; stable under inserts.
- **Offset pagination** for bounded tables (`rankings`, `points-table`,
  `leaderboard`): `?page=&page_size=` with total count.
- **Allow-listed sort fields** per endpoint (e.g. matches:
  `start_time_utc,-start_time_utc`; players: `name,runs,wickets,average`).
- **Filters** are AND-combined; repeated params are OR-combined where sensible.

## 6. Authentication & authorization

- All §2 read endpoints are **public/read-only**.
- Admin/ops endpoints (`/admin/...`, correction, ingestion) require **JWT/OAuth2**
  with RBAC roles (`viewer`, `operator`, `admin`, `data_steward`).
- Public writes: **none**, except optional user fantasy-team creation in a future
  version, which would be a separate authenticated surface.

## 7. Rate limiting & abuse prevention

- Token-bucket per IP and per API key; stricter on `deliveries`/`commentary`.
- `Cache-Control` + CDN absorbs read bursts (see [CRICKET_PERFORMANCE_AND_SCALING.md](CRICKET_PERFORMANCE_AND_SCALING.md)).
- `429` with `Retry-After`; `503` only when the provider is down **and** no cache.
- Request deduplication at the CDN/edge via `ETag`/`If-None-Match`.

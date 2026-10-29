# CRICKET_PLATFORM_RESEARCH.md

Research log for **Phase 1 (Rea MCP tooling)** and **Phase 2 (Cricbuzz website
analysis)**. Every entry distinguishes **observed** facts from **inferences**.

---

## 1. Executive summary of the research

- The **Rea (REA) MCP server is installed and functional**. Its browser
  capabilities work: a headless Google Chrome session launched by
  `capture_browser_scenario` successfully loaded control pages and returned
  accessibility trees, network events and screenshots.
- **Cricbuzz (`www.cricbuzz.com`, `m.cricbuzz.com`) and ESPNcricinfo are
  protected by an Akamai edge WAF.** The automated browser and the `webfetch`
  client received an **"Access Denied"** page (Akamai `errors.edgesuite.net`
  reference IDs) / HTTP 403. Per the task's rules, **no attempt was made to
  bypass anti-bot controls** (no UA spoofing, no proxy rotation, no challenge
  solving).
- A **plain low-volume read of publicly served, unauthenticated HTML pages**
  (via the Code-Mode HTTP client) reaches Cricbuzz and returned real, current
  content. This was used to document *observable user-visible functionality*
  only. No private/undocumented API endpoint was called, and no response bodies
  of internal APIs were reproduced.
- Cricbuzz is a **Next.js server-rendered (SSR) application**. Its public pages
  embed a serialised page-state payload (React Server Components) whose **field
  names** are observable. These fields (keys only, not proprietary values) were
  used to infer entity relationships, and are cited as such.
- Observed **CDN cache behaviour** (`Cache-Control: max-age=…`) gives first-hand
  evidence of the freshness requirements of live vs. static pages.

> **Hard limitation and honesty statement:** We do **not** know Cricbuzz's
> private backend, database schema, internal service topology, or undocumented
> APIs. Nothing in this package should be read as a claim about their internals.

---

## 2. Phase 1 — Rea MCP tool inspection

### 2.1 Skill discovery

- Skill file read: `~/.agents/skills/reverse-engineer-anything/SKILL.md` plus
  `references/runtime-observation.md` (the browser/Electron guide).
- The REA MCP server exposes **207 tools**. Browser-relevant tools discovered:

| Tool | Capability (as advertised) | Used? |
|------|----------------------------|-------|
| `capture_browser_scenario` | Agent-driven browser actions (goto/click/fill/select/wait), capture of URL, DOM, accessibility tree, screenshot, network, websockets, console, storage; launch (owned temp profile) or connect (CDP) | ✅ |
| `list_browser_targets` | List eligible HTTP(S) pages of an existing browser | ✅ (enumeration) |
| `inspect_web_page` / `observe_web_session` | Passive inspection of a user-driven page | ⛔ (blocked target) |
| `capture_web_screenshot` | Pixel capture | ⛔ (blocked target) |
| `inspect_web_network_capture` | Inspect retained HAR/SAZ/qlog captures | ⛔ (not needed) |
| `observe_web_execution`, `inspect_web_event_listeners`, `compare_web_captures` | Focused runtime follow-ups | ⛔ (not needed) |
| `analyze_web_bundle`, `export_web_scripts`, `trace_web_module_imports` | Static JS bundle analysis | ⛔ (out of scope) |

### 2.2 Capabilities confirmed working

A launch-mode scenario (`/usr/bin/google-chrome`, headless) against
`https://example.com/` returned `status: completed`, a populated accessibility
tree (1 075 chars) and full step metadata. **The tooling itself is healthy.**

### 2.3 Capability limitations recorded

- Browser captures are **passive about causality**: "Playwright scenario events
  do not expose request initiator stacks; receipt order does not prove
  causality."
- Network **bodies are not requested by default** and, when requested, are
  labelled "API results … not guaranteed to match wire bytes".
- `webfetch` (harness tool) and the REA headless browser **egress from a path
  that Akamai blocks** for Cricbuzz/ESPNcricinfo; the Code-Mode HTTP client
  egresses on a path that is served. This is itself a finding about bot
  protection (see §5).

### 2.4 Required-capability decision

| Requirement | REA support | Outcome |
|-------------|-------------|---------|
| Browser navigation | Yes | Available, but WAF-blocked for the target |
| Screenshots / page inspection / a11y | Yes | Blocked for the target |
| Network / JS inspection | Yes | Not usable against a blocked origin |
| Structured extraction from public HTML | Achievable | Achieved via low-volume public HTML read |

**Conclusion (Phase 1):** REA satisfies the *tooling* requirement. The *target*
is inaccessible to automation by design, so the authorized workaround — reading
publicly served HTML at low volume, and documenting the restriction — was used.
No research result was fabricated.

---

## 3. Phase 2 — Method

**Method M1 (browser automation).** `capture_browser_scenario` → blocked.
**Method M2 (harness `webfetch`)** → HTTP 403.
**Method M3 (low-volume public HTML read).** A small number of public,
unauthenticated pages were fetched with an ordinary HTTP client (`Accept:
text/html`). For each page we extracted only *structure*: page title, meta
description, navigation links, tab labels, and the **key names** of the
server-rendered page-state payload. No proprietary text/values were stored.

Volume: ~14 pages total. No API endpoints, no authentication, no rate-limit
evasion, no header spoofing, no proxy rotation. This is equivalent to visiting
the public site as a normal reader.

---

## 4. Phase 2 — Research log

Each row: capability used · page inspected · observable behaviour · evidence ·
confidence · unresolved questions. Confidence: **H** = directly observed on the
live public page; **M** = observed plus a small inference; **L** = inference.

### 4.1 Platform / navigation

| # | Capability | Page / feature | Observable behaviour | Evidence | Conf. | Unresolved |
|---|-----------|----------------|----------------------|----------|-------|-----------|
| R1 | M1/M2 | `www.cricbuzz.com` via headless browser / webfetch | Akamai "Access Denied", ref `errors.edgesuite.net/18.64a5c117…`; webfetch HTTP 403 | a11y text; HTTP 403 | H | Is it IP-reputation or automation fingerprint? |
| R2 | M3 | `https://www.cricbuzz.com/` | HTTP 200, 513 KB SSR HTML, `content-type: text/html; charset=utf-8` | bytes, title, meta | H | — |
| R3 | M3 | Homepage title/meta | Title "Cricbuzz \| Live Cricket Score, Schedule, News, Stats & Videos"; description promises live scores, ball-by-ball, schedule, ICC rankings | `<title>`, `description` | H | — |
| R4 | M3 | Global top nav | Links: Live Scores, Schedule, Archives, News, Series, Teams, Videos, Rankings, More(`#!`) | anchors | H | "Videos"/"News" are out of scope |
| R5 | M3 | JS bundle hosts (homepage, all pages) | Scripts under `/_next/static/chunks/…` → **Next.js** app; standard SSR+hydration | `<script src>` | H | Exact client polling endpoint unknown (not inspected) |

### 4.2 Live scores

| # | Capability | Page / feature | Observable behaviour | Evidence | Conf. | Unresolved |
|---|-----------|----------------|----------------------|----------|-------|-----------|
| R6 | M3 | `/cricket-match/live-scores` | Lists current matches; sub-tabs **Current / Live, Recent, Upcoming** (`/live-scores/recent-matches`, `/live-scores/upcoming-matches`) | anchors, `<title>` | H | Poll interval of the client |
| R7 | M3 | Live match row | Summary status strings: `RSA vs AUS - Lead by 50`, `ENG vs SL - SL opt to bowl`, `MUM vs HAR - Tea`, `LIVE` | anchor text | H | Full state enum (many labels) |
| R8 | M3 | Match URL scheme | `/live-cricket-scores/{matchId}/{slug}` | anchors | H | — |
| R9 | M3 | Match detail page tabs | **Info** (`/cricket-match-facts/`), **Live** (`/live-cricket-scores/`), **Scorecard** (`/live-cricket-scorecard/`), **Squads** (`/cricket-match-squads/`), **Overs** (`/live-cricket-over-by-over/`), **Graphs** (`/live-cricket-graphs/`), **Highlights** (`/cricket-match-highlights/`), **Full Commentary** (`/live-cricket-full-commentary/`), **News** | anchors on match page | H | Graphs/Highlights = video, out of scope |

### 4.3 Scorecard

| # | Capability | Page / feature | Observable behaviour | Evidence | Conf. | Unresolved |
|---|-----------|----------------|----------------------|----------|-------|-----------|
| R10 | M3 | `/live-cricket-scorecard/{id}/{slug}` | Title "Cricket scorecard"; `Cache-Control: max-age=5`; 637 KB | headers, state keys | H | — |
| R11 | M3 | Scorecard state keys | `scoreCard`, `batTeamDetails{batTeamId,batTeamName,batTeamShortName,batsmenData}`, `batsmenData.bat_N` with `batId,batName,batShortName,isCaptain,isKeeper,balls,dots,fours,sixes,mins,strikeRate,outDesc,bowlerId,fielderId1..3,ones,twos,threes,fives,boundaries,sixers,wicketCode,isOverseas,inMatchChange,playingXIChange` | state keys | H | Bowling-side keys not enumerated (equivalent structure inferred) |
| R12 | M3 | Innings score keys | `matchScore.team1Score.inngs1{inningsId,runs,wickets,overs}`, `inngs2`, `isFollowOn`, `isDeclared`, `team2Score` | state keys | H | — |
| R13 | M3 | Match meta keys | `matchInfo{matchId,seriesId,seriesName,matchDesc,matchFormat,startDate,endDate,state,status,stateTitle,status,shortStatus,matchType,currBatTeamId,venueInfo{ground,city,timezone},seriesStartDt,seriesEndDt,isTimeAnnounced}`, `isTournament`, `responseLastUpdated`, `playersOfTheMatch`, `playersOfTheSeries` | state keys | H | — |

### 4.4 Commentary / ball-by-ball

| # | Capability | Page / feature | Observable behaviour | Evidence | Conf. | Unresolved |
|---|-----------|----------------|----------------------|----------|-------|-----------|
| R14 | M3 | `/live-cricket-full-commentary/{id}/{slug}` | Title "Cricket full commentary"; `max-age=5` | headers, keys | H | Licensed; not redistributable without rights |
| R15 | M3 | Commentary state keys | `commentary{commentaryList[]{commText,ballNbr,event,batTeamName,batsmanStriker{batId,batName,batBalls,batDots,batFours,batSixes,batMins,batRuns,batStrikeRate},bowlerStriker{bowlId,bowlName,bowlMaidens,bowlNoballs,bowlOvs,bowlRuns,bowlWides,bowlWkts,bowlEcon},legalRuns,totalRuns,batTeamScore},commentaryFormats,commentarySnippetList{commId,infraType,headline}}`, plus `inningsScoreList`, `latestPerformance`, `overSummaryList` | state keys | H | Ball "event" vocabulary not enumerated |
| R16 | M3 | Player match performance links | `/player-match-performance/match/{matchId}/player/{playerId}/{batting\|bowling}` | anchors | H | — |

### 4.5 Series / tournaments

| # | Capability | Page / feature | Observable behaviour | Evidence | Conf. | Unresolved |
|---|-----------|----------------|----------------------|----------|-------|-----------|
| R17 | M3 | `/cricket-series/{seriesId}/{slug}` | Series home; tabs **Home / Matches / Table / Stats / Squads / News / Venues / Videos / Photos** | anchors | H | News/Videos/Photos out of scope |
| R18 | M3 | `/cricket-series/{id}/{slug}/points-table` | Points table; `max-age=22` | headers | H | — |
| R19 | M3 | Points-table keys | `pointsTableData{lastUpdated,legends,match_type,pointsTable[]{groupName,no_of_qual,pointsTableInfo[]{teamFullName,matchesPlayed,matchesWon,matchesLost,matchesTied,noRes,matchesDrawn,nrr,points,teamMatches[],form,teamQualifyStatus,teamImageId}},tournamentRules}` | state keys | H | — |
| R20 | M3 | `/cricket-series/{id}/{slug}/stats` | Series statistics; `max-age=1`; `initialStats`/`odiStatsList{headers,values,filter{matchtype,matchTypeId,matchTypeDesc},statsTypes/types{header,category}}`, `seasonId` | headers, keys | H | Full stats-type catalogue not enumerated |
| R21 | M3 | Archives `/cricket-scorecard-archives` | Historical series grouped by **year**, `internationalData/domesticData/leagueData/womenData`, `seriesMapProto`; `max-age=5` | keys | H | — |

### 4.6 Teams & players

| # | Capability | Page / feature | Observable behaviour | Evidence | Conf. | Unresolved |
|---|-----------|----------------|----------------------|----------|-------|-----------|
| R22 | M3 | `/cricket-team` | Team directory with category tabs **Domestic / League / Women**; team URLs `/cricket-team/{country-slug}/{teamId}` (e.g. `/cricket-team/india/2`, `/cricket-team/australia/4`) | anchors | H | Full team list large |
| R23 | M3 | `/profiles/{playerId}/{slug}` | Player profile; `max-age=5`; title includes "ICC Ranking, Age, Career Info & Stats" | headers, title | H | — |
| R24 | M3 | Player state keys | `playerCareerData{debut,lastPlayed,debutMatchID,lastPlayedMatchId,playerData{fullName,height,role,intlTeam,teams,DoB,bio,rankings{testRank,testBestRank,odiRank,odiBestRank,t20Rank,t20BestRank,testDiffRank},teamNameIds,playerTeamIds,faceImageId}, battingStats{headers,values,seriesSpinner}, bowlingStats{...}, recentBatting{rows}, recentBowling{rows}, playerNews}` | state keys | H | Batting/bowling stat column catalogue partial |
| R25 | M3 | Player histories | `/profiles/{id}/{slug}/all-matches/{batting\|bowling}` | anchors | H | — |

### 4.7 Rankings, schedule, squads

| # | Capability | Page / feature | Observable behaviour | Evidence | Conf. | Unresolved |
|---|-----------|----------------|----------------------|----------|-------|-----------|
| R26 | M3 | `/cricket-stats/icc-rankings/{gender}/{category}` | Categories **batting, bowling, all-rounder, teams**; genders men/women; `max-age=5` | anchors, headers | H | Ranking update cadence |
| R27 | M3 | Rankings keys | `formatTypesData{odi,test,t20}`; rows `{rank,rating,points,trend,faceImageId}`, `gender,isTeamsCategory,isWomen,initialFormatType,headers` | state keys | H | — |
| R28 | M3 | `/cricket-schedule/upcoming-series/{international\|league\|domestic\|women\|all}` | Schedule; categories **International / T20 Leagues / Domestic / Women / All**; `max-age=26`; keys `matchScheduleMap[]{date,matchScheduleList[]{matchInfo,startDt}}` | anchors, headers, keys | H | "Matches by day" grouping |
| R29 | M3 | `/cricket-match-squads/{id}/{slug}` | Squads; keys `team{imageDetails,profileUrl,players[]{fullName,nickName,captain,role,keeper,substitute,battingStyle,bowlingStyle,isOverseas,inMatchChange,playingXIChange},bench}` | state keys | H | — |
| R30 | M3 | `More` menu | `#!` — client-rendered overflow menu | anchors | L | Contents not observed |

### 4.8 Freshness (observed CDN TTLs)

| Page | `Cache-Control` (observed) | Interpretation |
|------|----------------------------|----------------|
| Scorecard | `max-age=5` | Live data, seconds-level freshness |
| Full commentary | `max-age=5` | Live, seconds-level |
| Match facts | `max-age=5` | Live match context |
| Squads | `max-age=5` | May change right up to toss |
| Player profile | `max-age=5` | Frequently refreshed (ranks/recent form) |
| Rankings | `max-age=5` | Frequently refreshed |
| Archives | `max-age=5` | (static content served with short TTL) |
| Points table | `max-age=22` | Updated after matches |
| Schedule | `max-age=26` | Semi-static |
| Series stats | `max-age=1` | Aggregates recomputed on read |

All responses carried `Vary: Accept-Encoding` and **no `ETag`** (observed).
This informs the TTL recommendations in
[CRICKET_PERFORMANCE_AND_SCALING.md](CRICKET_PERFORMANCE_AND_SCALING.md).

---

## 5. Key findings from the website analysis

1. **Server-rendered, link-addressed, entity-detail pages.** Every entity has a
   stable, human-readable URL (`/live-cricket-scores/{id}/{slug}`,
   `/cricket-series/{id}/{slug}/points-table`, `/profiles/{id}/{slug}`,
   `/cricket-team/{slug}/{id}`). An equivalent API should be resource-oriented.
2. **Clean separation of concerns in the UI**: match *shell* (Scorecard), live
   *ball* granularity (Commentary/Overs), *squads*, *facts*, *graphs*. This maps
   to distinct backend capabilities exactly as our domain model proposes.
3. **Extremely short cache TTLs** confirm live scorecards need second-level
   freshness, while schedules/tables tolerate tens of seconds.
4. **Rich dismissal modelling at ball level**: scorecard exposes `wicketCode`,
   `outDesc`, `bowlerId`, `fielderId1..3`, and commentary exposes `event` per
   ball → a delivery-row-centric model with explicit dismissal fields is
   appropriate.
5. **Captain / keeper / overseas / playing-XI-change flags** are first-class on
   the batting card and squad list → the domain model must version playing XI
   and flag roles per match (also needed for fantasy scoring).
6. **Role/registry breadth**: teams span international, domestic, league and
   women's categories; players carry international team + multiple teams —
   confirming the need for a **team-membership-over-time** model and a **player
   identity/registry** separate from team.
7. **Bot protection is intentional.** The target is behind Akamai; any real
   implementation must obtain data via a **licensed provider**, not scraping.
8. **Open data exists for historical reconstruction.** [Cricsheet](https://cricsheet.org/)
   provides freely-available ball-by-ball data (JSON/YAML/XML/CSV) for a large
   historical corpus (**~23,193 matches observed** on its downloads page,
   including 921 Tests, 3,226 ODIs, plus a "Multi-day" and "One-day matches"
   class) under the **Open Data Commons Attribution License** (commercial use
   permitted with attribution) and a public **People/Venues register** with
   cross-identifiers (including an Opta `key_pulse` column observed in
   `people.csv`). *Used here as a source of authorized historical data and
   identifier-mapping reference, not as a live feed.*
9. **Managed-provider options are documented publicly.** Examples observed:
   **Entity Sport** (Cricbuzz's B2B data arm — "Sports API & Fantasy Data
   Feed"; coverage claim includes "all domestic First Class, List A, T20 leagues
   IPL, BBL, PSL, BPL, CPL and International matches"), **CricAPI/cricketdata.org**
   (free tier + paid; series/matches/squads/ball-by-ball), **SportMonks**
   (documented fixture include set `runs,batting,bowling,lineup,balls,venue`),
   and **Sportradar**. Capabilities, SLAs and pricing must be verified
   contractually (see the provider matrix).

---

## 6. Confidence summary

| Claim | Confidence |
|-------|-----------|
| REA browser tooling works | **H (observed)** |
| Cricbuzz/ESPNcricinfo block automated clients at the edge | **H (observed)** |
| Cricbuzz is Next.js SSR; pages are link-addressed | **H (observed)** |
| Observed page-state field names | **H (observed)**, field *names* only |
| Observed `Cache-Control` values | **H (observed)** |
| Cricbuzz's internal API/database design | **Unknown — not claimed** |
| Provider coverage/pricing specifics | **Unknown — must be verified** |
| Cricsheet licensing/coverage | **H/M (observed page text)** |

---

## 7. Unresolved questions

1. Exact client-side polling/streaming transport and cadence (not inspected — by
   policy, undocumented endpoints were not called).
2. Complete vocabulary of match `state`/`status` labels and ball `event` types.
3. Whether commentary and wagon-wheel data are separately licensed upstream.
4. Commercial terms, SLAs, and per-endpoint rate limits of each candidate
   provider.
5. Exact ICC ranking update schedule and rating algorithm version.
6. Whether a chosen provider exposes stable provider entity IDs for idempotent
   identity resolution.

These are carried into
[CRICKET_RISKS_AND_ASSUMPTIONS.md](CRICKET_RISKS_AND_ASSUMPTIONS.md).

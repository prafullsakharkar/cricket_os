# CRICKET_DOMAIN_MODEL.md

Normalised, extensible cricket domain model. This document defines **every
entity**, its purpose, fields/types, keys, constraints, indexes, nullability,
relationships, classification (source / derived / cache), versioning need, and
an example record.

See [CRICKET_DATABASE_SCHEMA.md](CRICKET_DATABASE_SCHEMA.md) for the Mermaid ER
diagram and PostgreSQL DDL.

---

## 0. Global conventions

| Convention | Decision |
|---|---|
| Internal PK | `uuid` (UUIDv7, time-ordered) generated in the application layer; stored as `uuid`. Justified in the DB doc. |
| Provider IDs | Stored as `text` in the same row (e.g. `provider_match_id`) **plus** a generic `provider_entity_map` table for multi-provider mapping. Never used as a PK. |
| Timestamps | `timestamptz`, always UTC. Source's local date/time kept in `source_*` columns + `source_timezone`. |
| Money/points | `numeric(12,2)` where fractional (e.g. NRR), `integer` where integral (runs, wickets, points). |
| Soft delete | Reference entities use `is_active boolean` + `deleted_at timestamptz null`; transactional entities are append-only. |
| Provenance | Every ingestion-sourced table has `source_provider_id`, `provider_updated_at`, `ingested_at`, `revision`. |
| Enumerations | PostgreSQL `ENUM` types for stable sets; reference tables for extensible sets (e.g. `dismissal_type` as a table *and* a stable enum view). |
| Ordering | Ball/over sequence stored explicitly (`innings_number`, `over_number`, `ball_in_over`, `delivery_seq`) — never inferred from timestamps. |
| Classifications | **SRC** = normalised source data; **DRV** = derived (computed from SRC); **CACHE** = materialised projection for reads. |

---

## 1. Core reference data

### 1.1 Country
- **Purpose:** ISO-style country/region reference for players, teams, venues.
- **Fields:** `id uuid PK`; `iso_code char(2) null` (unique); `iso3 char(3) null`; `name text not null` (unique); `short_name text`; `flag_image_url text`; `is_active bool not null default true`; `source_provider_id uuid null FK providers`.
- **Unique:** `iso_code`, `name`.
- **Indexes:** `ux_country_iso`, `ux_country_name`.
- **Relationships:** 1 Country → N Team (home), 1 → N Player (birth country), 1 → N Venue.
- **Class:** SRC. **Versioning:** no (editable reference).
- **Example:** `{name:"India", iso_code:"IN", iso3:"IND", short_name:"IND"}`.

### 1.2 GoverningBody
- **Purpose:** ICC, regional bodies, national boards (owner/sanctioning authority).
- **Fields:** `id uuid PK`; `name text not null` (unique); `abbreviation text`; `country_id uuid null FK country`; `parent_id uuid null FK governing_body` (self, hierarchy); `type text not null CHECK in ('international','regional','national','franchise_league')`.
- **Indexes:** `ux_gov_body_name`, `ix_gov_body_country`.
- **Class:** SRC. **Versioning:** no.
- **Example:** `{name:"International Cricket Council", abbreviation:"ICC", type:"international"}`.

### 1.3 Venue
- **Purpose:** A place where cricket may be played (city + stadium + ground).
- **Fields:** `id uuid PK`; `name text not null`; `city text`; `country_id uuid null FK country`; `timezone text not null default 'UTC'`; `latitude numeric(9,6) null`; `longitude numeric(9,6) null`; `capacity int null`; `is_neutral bool not null default false`; `provider_venue_id text null`; `source_provider_id uuid null`.
- **Unique:** `(name, city)` (nullable-safe with `coalesce`).
- **Indexes:** `ux_venue_name_city`, `ix_venue_country`, `ix_venue_provider_id`.
- **Relationships:** 1 Venue → N Ground; 1 → N Match (host).
- **Class:** SRC. **Versioning:** no.
- **Example:** `{name:"Newlands", city:"Cape Town", country:ZA, timezone:"Africa/Johannesburg", lat:-33.978, lon:18.466}`.

### 1.4 Ground
- **Purpose:** A specific pitch/oval configuration at a venue (venues can host
  multiple grounds/ovals). Kept separate for future multi-ground venues.
- **Fields:** `id uuid PK`; `venue_id uuid not null FK venue`; `name text not null`; `notes text`.
- **Unique:** `(venue_id, name)`.
- **Class:** SRC (optional). **Versioning:** no.

### 1.5 Team
- **Purpose:** A cricket team (national, franchise, domestic, women's).
- **Fields:** `id uuid PK`; `name text not null`; `short_name text`; `abbreviation text` (e.g. `IND`); `team_type enum_team_type not null` (`international|franchise|domestic|women_international|women_franchise|other`); `country_id uuid null FK country`; `governing_body_id uuid null FK governing_body`; `home_venue_id uuid null FK venue`; `gender char(1) CHECK in ('M','W','X') default 'M'`; `is_international bool`; `logo_url text`; `provider_team_id text null`; `source_provider_id uuid`; `is_active bool`.
- **Unique:** `ux_team_provider_uniq (source_provider_id, provider_team_id)`; `(name, team_type, country_id)`.
- **Indexes:** `ix_team_type`, `ix_team_country`, `ix_team_name_trgm` (trigram for search).
- **Relationships:** N Team ↔ N Player via `TeamMembership`; 1 → N MatchTeam (via `MatchTeam`); 1 → N Squad.
- **Class:** SRC. **Versioning:** no (identity stable); history via `TeamMembership`.
- **Example:** `{name:"Mumbai Indians", short_name:"MI", team_type:"franchise", country:IN}`.

### 1.6 Player
- **Purpose:** Stable identity of a person who plays/played cricket.
- **Fields:** `id uuid PK`; `full_name text not null`; `known_as text`; `unique_name text` (slug-like, matches provider "unique name"); `date_of_birth date null`; `date_of_death date null`; `birth_country_id uuid null FK country`; `birth_place text`; `gender char(1) default 'M'`; `height_cm int null`; `batting_style enum_bat_style null` (`right_hand|left_hand`); `bowling_style enum_bowl_style null` (see 1.10); `primary_role enum_player_role null` (see 1.9); `is_wicketkeeper bool default false`; `international_team_id uuid null FK team`; `face_image_url text`; `bio text`; `provider_player_id text null`; `source_provider_id uuid`; `is_active bool`.
- **Unique:** `ux_player_provider_uniq(source_provider_id, provider_player_id)`; `name_key` (generated: normalised `full_name`+`dob`) to aid de-duplication.
- **Indexes:** `ix_player_dob`, `ix_player_intl_team`, `ix_player_name_trgm`.
- **Relationships:** N ↔ N Team (`TeamMembership`); 1 → N `MatchPlayer`; 1 → N `BattingEntry`/`BowlingEntry`/`Delivery`; 1 → N stats.
- **Class:** SRC. **Versioning:** Identity is stable; attribute changes logged in `data_correction`/`player_merge`.
- **Example:** `{full_name:"Patrick James Cummins", known_as:"Pat Cummins", dob:1993-05-08, bowling_style:"right_arm_fast", primary_role:"bowler", intl_team:"Australia"}`.
- **Note:** Provider ID for the observed example was `8095` (R23). Cricsheet's
  register exposes cross-identifiers (incl. Opta `key_pulse`) — ideal for the
  identity-mapping table (§6).

### 1.7 PlayerRole / 1.8 PlayingStyle (reference enums)
- **PlayerRole** values: `batter, bowler, all_rounder, wicketkeeper_batter, batting_all_rounder, bowling_all_rounder, all_rounder_wk` (superset of provider labels).
- **BattingStyle:** `right_hand, left_hand`.
- **BowlingStyle:** `right_arm_fast, right_arm_medium, right_arm_offbreak, right_arm_legbreak, right_arm_googly/offbreak, left_arm_fast, left_arm_medium, left_arm_orthodox, left_arm_chinaman, none`.
- **Class:** reference. Stored as enums; catalogue table `role_catalogue` optional.

### 1.9 TeamMembership
- **Purpose:** Time-bounded affiliation of a player to a team (transfers,
  debut, retirement). Answers "player identities maintained across teams and
  competitions" and "participation changes over time".
- **Fields:** `id uuid PK`; `team_id uuid not null FK team`; `player_id uuid not null FK player`; `joined_at date null`; `left_at date null`; `membership_type text` (`squad|guest|captain|coach`); `is_captain bool`; `is_primary bool`; `source_provider_id uuid`.
- **Unique:** `ux_membership_open (team_id, player_id, coalesce(left_at,'infinity'))`.
- **Indexes:** `ix_membership_player`, `ix_membership_team_active`.
- **Class:** SRC. **Versioning:** the table *is* the history (temporal).
- **Example:** `{team:"Australia", player:"Pat Cummins", joined_at:2011-10-17, left_at:null, is_captain:true}`.

### 1.10 Squad
- **Purpose:** A named set of players for a series/tournament (pre-tournament
  announced squad) — distinct from match Playing XI.
- **Fields:** `id uuid PK`; `team_id uuid not null FK team`; `series_id uuid null FK series`; `season_id uuid null FK season`; `name text`; `announced_at timestamptz`; `source_provider_id uuid`.
- **Class:** SRC. **Versioning:** append new squad rows; do not mutate.
- **Link table:** `SquadMember(squad_id, player_id, role, is_captain, is_keeper, is_substitute, status)` key `(squad_id, player_id)`.

### 1.11 Competition
- **Purpose:** The stable *concept* of a competition (e.g. "Indian Premier
  League", "Ranji Trophy", "ICC Cricket World Cup") independent of a season.
- **Fields:** `id uuid PK`; `name text not null`; `short_name text`; `competition_type enum_competition_type` (`international|league|domestic|multi_nation|women`); `governing_body_id uuid null`; `format_type enum_format_type null` (default format); `is_international bool`; `gender char(1)`; `provider_competition_id text null`; `source_provider_id uuid`.
- **Unique:** `ux_competition_provider(source_provider_id, provider_competition_id)`; `name`.
- **Class:** SRC. **Versioning:** no; seasons carry history.

### 1.12 Series
- **Purpose:** An edition of a competition/tour at a point in time (what
  Cricbuzz calls a "series"; observed `seriesId`). e.g. "Australia tour of South
  Africa, 2026" or "IPL 2026".
- **Fields:** `id uuid PK`; `name text not null`; `short_name text`; `competition_id uuid null FK competition`; `season_id uuid null FK season`; `series_type enum_series_type` (`bilateral|triangular|tournament|league|icc_event`); `start_date date`; `end_date date`; `host_country_id uuid null`; `is_tournament bool not null default false`; `points_system jsonb null` (win/tie/nr/bonus points); `provider_series_id text null`; `source_provider_id uuid`.
- **Unique:** `ux_series_provider(source_provider_id, provider_series_id)`; `(name, start_date)`.
- **Indexes:** `ix_series_dates` (btree on `start_date`), `ix_series_type`.
- **Class:** SRC. **Versioning:** no; corrections via `data_correction`.
- **Example:** `{name:"Pakistan ODI Tri-Series, 2026", is_tournament:true, points_system:{win:2,tie:1,nr:1}}`.

### 1.13 Season
- **Purpose:** A labelled period within a competition (e.g. "2023-27" for a
  League Two cycle, or "2026").
- **Fields:** `id uuid PK`; `competition_id uuid null FK competition`; `label text not null`; `start_date date`; `end_date date`.
- **Unique:** `(competition_id, label)`.
- **Class:** SRC. **Versioning:** no.

### 1.14 MatchOfficials
- **Purpose:** Umpires, referees, third umpire, match referee.
- **Fields:** `id uuid PK`; `match_id uuid not null FK match`; `official_name text`; `official_player_id uuid null FK player`; `role text` (`umpire1|umpire2|third_umpire|reserve_umpire|referee|tv_umpire`); `country_id uuid null`.
- **Unique:** `(match_id, role)`.
- **Class:** SRC.
- **Example:** `{match:"RSA v AUS 3rd Test", official:"Kumar Dharmasena", role:"umpire1"}`.

### 1.15 PlayerMerge (merge audit + redirect map)
- **Purpose:** Records a player-identity de-duplication (merge two `Player` rows)
  and provides a redirect map: `merged_player_id` is superseded by
  `canonical_player_id`, and any reference to the merged id is rewritten/redirected
  to the canonical id. Needed because the same person appears under different
  provider ids (see §6.2).
- **Fields:** `id uuid PK`; `canonical_player_id uuid not null FK player`;
  `merged_player_id uuid not null FK player`; `reason text`; `merged_by uuid null`;
  `merged_at timestamptz not null default now()`.
- **Unique:** `merged_player_id` (each merged player redirects to exactly one
  canonical player). **Check:** `canonical_player_id <> merged_player_id`.
- **Indexes:** `ix_player_merge_canonical`.
- **Class:** SRC/audit (append-only). **Versioning:** the table is the history.
- **Example:** `{canonical:"Pat Cummins", merged:"P Cummins (dup)", reason:"same DoB + name key"}`.

### 1.16 PlayerDuplicateCandidate (steward review queue)
- **Purpose:** A scored (candidate, plausible-canonical) player pair awaiting
  steward review. Created by identity resolution whenever a new entity's best
  matches land above `DUPLICATE_CANDIDATE_THRESHOLD`; a steward merges or
  rejects each pair, so duplicate players never auto-merge (see
  [IDENTITY_RESOLUTION.md](IDENTITY_RESOLUTION.md)).
- **Fields:** `id uuid PK`; `candidate_player_id uuid not null FK player`;
  `canonical_player_id uuid not null FK player`; `score numeric(4,3) not null`;
  `feature_breakdown jsonb not null` (per-feature scores, raw values, any cap
  applied); `status text not null default 'open' CHECK in
  ('open','resolved','merged')`; `reviewed_by uuid null`; `reviewed_at
  timestamptz null`; `created_at timestamptz not null default now()`.
- **Unique:** `ux_dup_candidate (candidate_player_id, canonical_player_id)`.
- **Indexes:** `ix_dup_candidate_status (status, created_at DESC)`,
  `ix_dup_candidate_canonical (canonical_player_id)`.
- **Class:** SRC/operational (steward queue). **Versioning:** status transitions
  are audited via the `player_duplicate.reviewed` event.
- **Example:** `{candidate:"V Kohli", canonical:"Virat Kohli", score:0.94,
  breakdown:{name:0.99, dob:1.0, country:1.0}, status:"open"}`.

---

## 2. Match scheduling & participation

### 2.1 Match (aggregate root for a single game)
- **Purpose:** One scheduled/played game.
- **Fields:** `id uuid PK`; `series_id uuid null FK series`; `competition_id uuid null FK`; `season_id uuid null FK`; `format_type enum_format_type not null`; `match_number int null`; `match_desc text` (e.g. "3rd Test"); `venue_id uuid null FK venue`; `gender char(1)`; `start_time_utc timestamptz not null`; `end_time_utc timestamptz null`; `source_local_date date not null`; `source_timezone text`; `status enum_match_status not null default 'scheduled'`; `status_detail text` (short live label, e.g. "Lead by 50"); `state_title text` (Phase D: human-readable session label, e.g. "Day 4 — Tea"); `result_type enum_result_type null`; `result_text text`; `toss_winner_team_id uuid null FK team`; `toss_decision text null CHECK in ('bat','bowl','n/a')`; `winner_team_id uuid null FK team`; `is_follow_on bool default false`; `is_dls_applied bool default false`; `dls_par_score jsonb null`; `overs_limit int null` (e.g. 50, 20); `balls_per_over int not null default 6`; `provider_match_id text null`; `source_provider_id uuid`; `revision int not null default 1`; `last_ingested_at timestamptz null` (Phase D: stamped only when a live batch changed the match); `provider_updated_at timestamptz null`.
- **Unique:** `ux_match_provider(source_provider_id, provider_match_id)`.
- **Indexes:** `ix_match_start_time`, `ix_match_status_start`, `ix_match_series`, `ix_match_venue`.
- **Relationships:** 1 → N `MatchTeam`; 1 → 4 `Innings`; 1 → N `Delivery`; 1 → N `MatchPlayer`.
- **Class:** SRC (aggregate). **Versioning:** corrections via `data_correction`; keep `revision`.
- **Example:** `{format:"TEST", match_desc:"3rd Test", venue:"Newlands", start:"2026-10-27T08:00:00Z", status:"in_progress", status_detail:"AUS lead by 50", balls_per_over:6}`.

### 2.2 MatchFormat (reference) & FormatType enum
- `enum_format_type`: `TEST, ODI, T20I, T20, FIRST_CLASS, LIST_A, THE_HUNDRED, T10, OTHER`.
- A `format_catalogue` reference table maps format → `max_innings` (Test=4, others=2), `overs_limit`, `balls_per_over`, `allows_declaration`, `allows_follow_on`, `allows_draw`, `allows_super_over`.
- **Class:** reference. **Example:** `{TEST: {max_innings:4, overs_limit:null, allows_draw:true, allows_follow_on:true, allows_declaration:true}}`.

### 2.3 MatchStatus (reference) + transition rules
- `enum_match_status`: `SCHEDULED, TOSS, IN_PROGRESS, INNINGS_BREAK, DRINKS, RAIN_DELAY, BAD_LIGHT, STUMPS, DAY_BREAK, COMPLETED, ABANDONED, CANCELLED, FORFEITED, POSTPONED`.
- **`status` is the match lifecycle only.** The *outcome* lives in
  `enum_result_type` (`WIN, TIE, DRAW, NO_RESULT, ABANDONED, CANCELLED, FORFEIT,
  SUPER_OVER_TIE_BREAK`) and `MatchResult`. `TIED` and `NO_RESULT` are **not**
  status values: a tied match is `status=COMPLETED, result_type=TIE`; a no-result
  match is `status=COMPLETED` (or `ABANDONED`) with `result_type=NO_RESULT`.
- **Observed real labels** include `Tea`, `LIVE`, `Lead by 50`, `opt to bowl` (R7). These are *display* strings; we store them in `status_detail` while normalising to the enum in `status`.
- Transitions are validated in the scoring service (Phase 5 doc §match completion).
- **Phase D:** transitions are enforced by the explicit table in
  `apps/matches/services/state.py` (`ALLOWED_TRANSITIONS`); re-applying the
  current state is an idempotent no-op; every transition appends a
  `match.state_changed` event and updates `state_title`/`status_detail`.
  `state_title` is the human-readable session label; `last_ingested_at` is
  stamped only when a live batch changed the match (an empty poll never masks
  provider lag).

### 2.4 MatchStage
- **Purpose:** Stage within a tournament (group name, round, playoff).
- **Fields:** `id uuid PK`; `series_id uuid FK`; `name text`; `stage_type text` (`group|super_league|quarter_final|semi_final|final|playoff|league`); `group_name text`; `sequence int`.
- **Unique:** `(series_id, name)`.
- **Class:** SRC.
- **Example:** `{series:"ICC CWC League Two", name:"Group A", stage_type:"group"}`.

### 2.5 MatchTeam (participation)
- **Purpose:** Binds a Match to its two+ participating teams with per-match
  team metadata (also supports neutral/3rd teams in tri-series).
- **Fields:** `id uuid PK`; `match_id uuid not null FK`; `team_id uuid not null FK`; `team_number smallint not null CHECK in (1,2)`; `is_home bool`; `is_neutral_venue bool`; `provider_team_id text null`.
- **Unique:** `(match_id, team_id)`, `(match_id, team_number)`.
- **Class:** SRC.
- **Example:** `{match:"…", team:"Australia", team_number:2}`.

### 2.6 Toss
- **Purpose:** Record the toss as an event (also stored denormalised on Match).
- **Fields:** `id uuid PK`; `match_id uuid not null unique FK`; `winner_team_id uuid FK team`; `decision text` (`bat|bowl`); `toss_time timestamptz`.
- **Class:** SRC.

### 2.7 MatchPlayer (Playing XI / participation per match)
- **Purpose:** Who played, in what role, and their per-match team — the record
  that lets stats attach to a concrete match and supports substitutions.
- **Fields:** `id uuid PK`; `match_id uuid not null FK`; `player_id uuid not null FK`; `team_id uuid not null FK`; `squad_id uuid null`; `batting_position int null`; `is_captain bool default false`; `is_keeper bool default false`; `is_overseas bool default false`; `is_substitute bool default false` (impact/concussion sub); `is_playing_xi bool not null default true`; `playing_xi_change bool default false`; `in_match_change bool default false`; `group_label text` (`playing_xi|bench|substitute`).
- **Unique:** `(match_id, player_id)`.
- **Indexes:** `ix_matchplayer_player`, `ix_matchplayer_match_team`.
- **Class:** SRC. **Versioning:** append new rows if XI changes; flag changes.
- **Example:** `{match:"RSA v AUS 3rd Test", player:"Cooper Connolly", team:"Australia", is_captain:false, is_overseas:true, playing_xi_change:true}`.

### 2.8 SubstitutePlayer
- **Purpose:** Explicit substitution record (concussion/impact/tactical).
- **Fields:** `id uuid PK`; `match_id uuid FK`; `team_id uuid FK`; `player_out_id uuid FK player`; `player_in_id uuid FK player`; `reason text`; `at_innings_id uuid null`; `at_delivery_seq bigint null`.
- **Class:** SRC.

### 2.9 Innings
- **Purpose:** One innings (Test can have 4; limited-overs 2; Super Over
  separate innings flag).
- **Fields:** `id uuid PK`; `match_id uuid not null FK`; `innings_number smallint not null` (1..4); `batting_team_id uuid not null FK team`; `bowling_team_id uuid not null FK team`; `is_super_over bool not null default false`; `super_over_number smallint null`; `declared bool default false`; `forfeited bool default false`; `all_out bool default false`; `target int null` (runs to win); `target_balls int null`; `dls_target int null`; `is_follow_on_innings bool default false`; `status enum_innings_status` (`not_started|in_progress|declared|all_out|completed|forfeited`); `provider_innings_id text null`.
- **Unique:** `(match_id, innings_number, is_super_over, super_over_number)` (use `coalesce(super_over_number,0)`).
- **Indexes:** `ix_innings_match`.
- **Class:** SRC. **Example:** `{match:"…", innings_number:2, batting_team:"RSA", all_out:true, runs:329}`.

### 2.10 BattingInnings / BowlingInnings (views)
- Implemented as **generated views / read models** over `Innings` +
  `MatchTeam`: `BattingInnings` = innings joined to batting team totals;
  `BowlingInnings` = same innings viewed from the bowling team. No separate base
  table required (avoid duplicating innings). Documented as DRV.

### 2.11 BattingOrder
- **Purpose:** The order batters came in (distinct from XI order). Represented
  on `BattingEntry.position` (§3.6) rather than a separate table.

### 2.12 TargetAndFollowOn
- Modelled as columns on `Innings` (`target`, `target_balls`, `dls_target`,
  `is_follow_on_innings`) plus a `Match.is_follow_on`/`dls_par_score`. No
  separate table. DRV/SRC.

### 2.13 MatchResult
- **Purpose:** Normalised outcome.
- **Fields:** `id uuid PK`; `match_id uuid not null unique FK`; `winner_team_id uuid null`; `result_type enum_result_type` (`WIN|TIE|DRAW|NO_RESULT|ABANDONED|CANCELLED|FORFEIT|SUPER_OVER_TIE_BREAK`); `margin_type text null` (`runs|wickets|innings|tie|n/a`); `margin_value int null`; `result_text text`; `is_dls bool`; `is_super_over bool`; `completed_at timestamptz`.
- **Class:** DRV (from innings/status) but persisted for query.
- **Example:** `{result_type:"win", winner:"Australia", margin_type:"runs", margin_value:50, result_text:"Australia won by 50 runs"}`.

### 2.14 MatchAward
- **Purpose:** Player of the match / series awards.
- **Fields:** `id uuid PK`; `match_id uuid null FK`; `series_id uuid null FK`; `player_id uuid not null FK player`; `award_type text` (`player_of_match|player_of_series|batter_of_match|bowler_of_match`).
- **Unique:** `(match_id, award_type)` (nullable-safe).
- **Class:** SRC.

---

## 3. Ball-by-ball & scorecard data

### 3.1 Over
- **Purpose:** Group deliveries into overs; hold maiden/over-level data.
- **Fields:** `id uuid PK`; `innings_id uuid not null FK`; `over_number int not null`; `bowler_id uuid not null FK player`; `is_maiden bool default false`; `runs_conceded int default 0`; `wickets int default 0`; `is_complete bool default false`; `deliveries_count int`.
- **Unique:** `(innings_id, over_number)`.
- **Class:** DRV (materialised for query/perf) — canonical truth remains on
  `Delivery`.

### 3.2 Delivery (canonical ball-by-ball fact)
- **Purpose:** The atomic event. Everything else (scorecard, over, partnership,
  fall of wicket, aggregates) is derivable from here.
- **Fields:** `id uuid PK`; `match_id uuid not null FK`; `match_date date not null` (partition key); `innings_id uuid not null FK`; `over_number int not null`; `ball_in_over int not null` (legal-ball counter within over, 1..N, may exceed 6 under no-balls); `delivery_seq bigint not null` (monotonic per innings; authoritative ordering); `bowler_id uuid not null FK player`; `batter_id uuid not null FK player`; `non_striker_id uuid not null FK player`; `runs_off_bat int not null default 0`; `wides int not null default 0`; `noballs int not null default 0`; `byes int not null default 0`; `leg_byes int not null default 0`; `penalty_runs int not null default 0`; `total_runs int generated always as (runs_off_bat+wides+noballs+byes+leg_byes+penalty_runs) stored`; `is_wide bool generated ... stored`; `is_noball bool generated ... stored`; `is_legal bool generated always as (not is_wide and not is_noball) stored`; `is_wicket bool not null default false`; `dismissal_type enum_dismissal_type null`; `dismissed_player_id uuid null FK player`; `bowler_credited_wicket bool default false`; `fielder1_id uuid null FK player`; `fielder2_id uuid null FK player`; `fielder3_id uuid null FK player`; `shot_type text null`; `is_boundary_four bool`; `is_boundary_six bool`; `commentary_text text null` (licensed; may be excluded); `provider_delivery_id text null`; `source_provider_id uuid`; `revision int not null default 1`; `is_current bool not null default true`; `superseded_at timestamptz null`; `retired_player_id uuid null FK player`; `retirement_type enum_retirement_type null` (`HURT|NOT_OUT`).
- **Unique:** `ux_delivery_current(match_date, innings_id, delivery_seq) WHERE is_current` (exactly one current row per ball); `ux_delivery_revision(match_date, innings_id, delivery_seq, revision)` (full history); `ux_delivery_provider(match_date, source_provider_id, provider_delivery_id, revision)` (nullable-safe). Every unique index includes the partition key `match_date`.
- **Indexes:** `ix_delivery_match(match_date, match_id)`, `ix_delivery_batter`, `ix_delivery_bowler` (PK includes `match_date`).
- **Addressing:** services always address a delivery by `(match_date, innings_id, delivery_seq)` (derive `match_date` from the innings/match) so partition pruning applies. All selectors and rebuilds read `is_current = true` only.
- **Partitioning:** RANGE by `match_date` (see DB doc).
- **Class:** SRC. **Versioning:** corrections append a new `revision` row and set the
  previous row `is_current=false`/`superseded_at`, plus a `data_correction` entry.
  New fields: `is_current bool not null default true`; `superseded_at timestamptz null`;
  `retired_player_id uuid null FK player`; `retirement_type enum_retirement_type null`
  (`HURT|NOT_OUT`) — see §3.5.
- **Example (a wide + 1 run):** `{innings:"RSA 2nd", over_number:102, ball_in_over:5, delivery_seq:1234, bowler:"Rabada", batter:"Connolly", runs_off_bat:0, wides:1, total_runs:1, is_wide:true, is_legal:false}`.
- **Example (caught):** `{runs_off_bat:0, is_wicket:true, dismissal_type:"caught", dismissed_player_id:"KK Nair", bowler_credited_wicket:true, fielder1_id:"…", is_legal:true}`.

### 3.3 Batter / NonStriker
- Represented as `batter_id` / `non_striker_id` FKs on `Delivery`. Not separate
  tables.

### 3.4 Bowler
- `bowler_id` FK on `Delivery`/`Over`. Not separate.

### 3.5 Extras & Wicket/Dismissal
- Extras = `wides, noballs, byes, leg_byes, penalty_runs` columns (§3.2).
- Dismissal = `is_wicket`, `dismissal_type`, `dismissed_player_id`,
  `bowler_credited_wicket`, `fielder1..3_id` columns + a `dismissals` read model
  for fall-of-wicket reporting (§3.9). **Delivery-level detail is never lost.**
- **Wickets vs. retirements:** `is_wicket=true` only for dismissals that count
  in the team wicket tally and for `RETIRED_OUT`. Retired-hurt and
  retired-not-out are **not dismissals**. A retirement that coincides with a
  delivery is stored on that delivery as `retired_player_id` +
  `retirement_type` (`HURT|NOT_OUT`), with `is_wicket=false` and
  `dismissal_type=NULL`. A retirement with **no ball** (rain/drinks/innings
  break, before the first ball, or an innings that never resumes) is stored as an
  `InningsEvent` (§3.13) instead. Both are read through one merged accessor,
  `innings_retirements(innings)`, so there is a single logical source and no
  retirement is recorded twice. A retired-hurt batter may resume and counts as
  **not-out** in averages; `RETIRED_OUT` is a dismissal and counts as a team
  wicket. Retiring (`HURT`/`NOT_OUT`) ends the current partnership but adds no
  `FallOfWicket` row.
- `Delivery.is_legal` vs "recorded delivery": every ball bowled is *recorded*;
  only legal balls advance the over. `is_legal=false` for wides/no-balls.
  `ball_in_over` counts legal balls; `delivery_seq` counts all recorded balls.

### 3.6 BattingEntry (per-innings batting scorecard line)
- **Purpose:** Materialised batting card row (read model) for fast scorecards;
  fully derivable from `Delivery` + `MatchPlayer`.
- **Fields:** `id uuid PK`; `innings_id uuid not null FK`; `player_id uuid not null FK`; `team_id uuid FK`; `position int`; `is_captain bool`; `is_keeper bool`; `runs int`; `balls int`; `dots int`; `fours int`; `sixes int`; `ones/twos/threes/fives int`; `minutes int null`; `strike_rate numeric(8,3)`; `out_desc text`; `is_out bool`; `dismissal_type enum_dismissal_type null`; `bowler_id uuid null`; `fielder1_id/fielder2_id/fielder3_id uuid null`; `wicket_code text null`; `is_overseas bool`; `is_impact bool`; `revision int`.
- **Unique:** `(innings_id, player_id)`.
- **Class:** DRV/CACHE. **Versioning:** recomputed; `revision` bumped.

### 3.7 BowlingEntry (per-innings bowling scorecard line)
- **Fields:** `id uuid PK`; `innings_id uuid not null FK`; `player_id uuid not null FK`; `team_id uuid FK`; `overs_display text` (e.g. `"10.3"`); `legal_balls int`; `maidens int`; `runs_conceded int`; `wickets int`; `wides int`; `noballs int`; `economy numeric(8,3)`; `dots int`; `bowling_avg numeric(8,3) null`; `strike_rate numeric(8,3) null`; `revision int`.
- **Unique:** `(innings_id, player_id)`.
- **Class:** DRV/CACHE.

### 3.8 FallOfWicket
- **Fields:** `id uuid PK`; `innings_id uuid not null FK`; `wicket_number int not null` (1..10); `score int`; `over_display text`; `delivery_seq bigint`; `dismissed_player_id uuid FK`; `is_partnership_break bool`.
- **Unique:** `(innings_id, wicket_number)`.
- **Class:** DRV. **Example:** `{wicket_number:7, score:379, over_display:"102.4", dismissed_player:"Cooper Connolly"}`.

### 3.9 Partnership
- **Fields:** `id uuid PK`; `innings_id uuid not null FK`; `wicket_number int not null` (0-indexed = for which wicket, 0 = 1st); `batter1_id uuid FK`; `batter2_id uuid FK`; `runs int`; `balls int`; `fours`; `sixes`; `start_delivery_seq bigint`; `end_delivery_seq bigint null`; `unbroken bool`.
- **Unique:** `(innings_id, wicket_number)`.
- **Class:** DRV.

### 3.10 InningsTotals
- Modelled on `Innings` (runs/wickets/overs derived) — no separate table; a
  `innings_summary` read model (CACHE) holds denormalised totals, including
  `batch_complete boolean not null default true` (false = "summary-only" innings
  with no ball-by-ball data; see DB doc §4.9).

### 3.11 Commentary
- Ball commentary text is **licensed**. Store `commentary_text` on `Delivery`
  when rights permit, else a separate `Commentary` table gated by licensing:
  `{id, delivery_id FK nullable, innings_id, ball_ref text, event text, text, language char(2), source_provider_id, is_licensed bool}`.
  If unlicensed, the field is omitted and the endpoint returns
  `403 license_required`. `commId`/`headline` (R15) map here.

### 3.12 MatchEventLog (realtime event stream)
- **Purpose:** Append-only per-match event stream underpinning realtime replay
  and reconnect. `seq` is a **monotonic PER-MATCH** event sequence (bigint),
  distinct from the per-innings `delivery_seq` (see §3.2).
- **Fields:** `id uuid PK`; `match_id uuid not null FK match`; `seq bigint not null`;
  `event_type text not null` (`delivery.appended`, `innings.updated`,
  `match.state_changed`, `match.completed`, `correction.applied`, ...);
  `payload jsonb not null default '{}'`; `created_at timestamptz not null default now()`.
- **Unique:** `(match_id, seq)`. **Indexes:** `ix_match_event_log(match_id, seq)`.
- **Retention:** kept for a configurable window after completion (default 24 h),
  then purged; a client whose `last_seq` predates the retained range receives
  `resync_required` and must REST-refetch.
- **Class:** operational/derived (replayable from canonical data).

### 3.13 InningsEvent (innings-level events without a ball)
- **Purpose:** Records innings-level events that are **not** tied to a delivery —
  a retirement during a rain delay / drinks / innings break, before the first
  ball, or in an innings that never resumes; an injury; a resumed innings. Keeps
  the `delivery` row purely ball-by-ball (a retirement that coincides with a ball
  stays on `delivery`, §3.5) while still capturing off-ball retirements.
- **Fields:** `id uuid PK`; `innings_id uuid not null FK innings`;
  `event_type enum_innings_event_type` (`RETIREMENT_HURT | RETIREMENT_NOT_OUT |
  RESUME | OTHER`); `player_id uuid null FK player` (required unless `OTHER`);
  `anchor_delivery_seq bigint null` (the delivery immediately **after** which the
  event takes effect; **NULL = before the first delivery**); `over_number int
  null`; `notes text null`; `announced_at timestamptz null`;
  `provider_event_id text null`; `source_provider_id uuid`;
  `revision int not null default 1`; `is_current bool not null default true`;
  `superseded_at timestamptz null`.
- **Unique:** `ux_innings_event_current(innings_id, event_type, coalesce(player_id,∅), coalesce(anchor_delivery_seq,-1)) WHERE is_current`;
  `ux_innings_event_revision(... , revision)`;
  `ux_innings_event_provider(source_provider_id, provider_event_id, revision)` (nullable-safe).
- **Accessor:** `innings_retirements(innings)` merges delivery-anchored and
  `InningsEvent` retirements into one ordered list; the scoring engine,
  partnership/FOW rebuilds and stats use only that accessor.
- **Class:** SRC. **Versioning:** corrections append a new `revision` and flip
  `is_current`, mirroring `Delivery` (§3.2).

---

## 4. Statistics & analytics

### 4.1 PlayerMatchStat (raw per-player per-match line, all facets)
- **Purpose:** Durable per-match fact for career aggregation and fantasy.
- **Fields:** `id uuid PK`; `match_id uuid not null FK`; `player_id uuid not null FK`; `team_id uuid FK`; `innings_id uuid null`; `did_bat bool`; `did_bowl bool`; `did_field bool`; `batting jsonb` (runs, balls, 4s,6s, dots, sr, out, dismissal_type); `bowling jsonb` (overs, balls, runs, wkts, maidens, wides, noballs, econ); `fielding jsonb` (catches, stumpings, runouts, dropped); `is_playing_xi bool`; `source_provider_id`.
- **Unique:** `(match_id, player_id)`.
- **Indexes:** `ix_pms_player`, `ix_pms_match`, GIN on `batting`/`bowling` optional.
- **Class:** DRV (from deliveries) but persisted (materialised) for speed.
- **Example:** `{match:"…", player:"Pat Cummins", bowling:{overs:"20.0", runs:70, wkts:3, maidens:5, econ:3.5}, batting:{runs:12, balls:30}}`.

### 4.2 BattingCareer / BowlingCareer / FieldingCareer (aggregates)
- **Purpose:** Career and format-scoped career aggregates. Canonical storage is
  `PlayerCareerStat` with a `scope` discriminator rather than three tables.
- **Fields:** `id uuid PK`; `player_id uuid not null FK`; `scope_type enum_stat_scope` (`career|format|season|series|team|venue|opposition`); `scope_ref_id uuid null` (e.g. series/season/team id); `format_type enum_format_type null`; `gender char(1)`; `matches int`; `innings int`; `runs int`; `balls int`; `not_outs int`; `high_score int`; `high_score_not_out bool`; `fifties int`; `hundreds int`; `fours int`; `sixes int`; `batting_avg numeric(8,2) null`; `strike_rate numeric(8,2) null`; `catches int`; `stumpings int`; `balls_bowled int`; `runs_conceded int`; `wickets int`; `maidens int`; `best_wickets int`; `best_runs int`; `bowling_avg numeric(8,2) null`; `economy numeric(8,2) null`; `bowling_sr numeric(8,2) null`; `five_wicket_hauls int`; `revision int`; `computed_at timestamptz`.
- **Unique:** `(player_id, scope_type, coalesce(scope_ref_id,'0000…'), coalesce(format_type,'any'), gender)`.
- **Indexes:** `ix_career_player_scope`, `ix_career_format`.
- **Class:** DRV/CACHE (recomputed; the DRV source is `PlayerMatchStat`).
- **Example:** `{player:"Pat Cummins", scope:"career", format:"TEST", wickets:294, bowling_avg:22.4, five_wicket_hauls:12}`.

### 4.3 TeamStat
- **Fields:** `id uuid PK`; `team_id uuid FK`; `scope_type enum_stat_scope`; `scope_ref_id uuid null`; `format_type enum_format_type null`; `matches/won/lost/tied/drawn/no_result int`; `runs_scored/runs_conceded int`; `wickets_taken int`; `nrr numeric(8,3) null`; `revision`; `computed_at`.
- **Unique:** `(team_id, scope_type, coalesce(scope_ref_id,'0'), coalesce(format_type,'any'))`.
- **Class:** DRV/CACHE.

### 4.4 HeadToHead
- **Fields:** `id uuid PK`; `team1_id uuid`; `team2_id uuid`; `format_type null`; `matches int`; `team1_wins int`; `team2_wins int`; `ties int`; `draws int`; `no_results int`; `last_match_id uuid null`; `computed_at`.
- **Unique:** `(least(team1_id,team2_id), greatest(team1_id,team2_id), coalesce(format_type,'any'))`.
- **Class:** DRV/CACHE.

### 4.5 PlayerRanking
- **Fields:** `id uuid PK`; `player_id uuid FK`; `team_id uuid null FK`; `gender char(1)`; `format_type enum_format_type`; `category enum_ranking_category` (`batting|bowling|all_rounder|team`); `rank int`; `rating int`; `points numeric(8,2) null`; `trend text null`; `best_rank int null`; `best_rating int null`; `as_of_date date not null`; `source_provider_id`.
- **Unique:** `(player_id, gender, format_type, category, as_of_date)` and `(gender, format_type, category, rank, as_of_date)`.
- **Indexes:** `ix_ranking_lookup(gender,format_type,category,rank)`.
- **Class:** SRC (provider-supplied ICC) — marked as supplied, not computed.
- **Example:** `{player:"Pat Cummins", gender:"M", format:"TEST", category:"bowling", rank:1, rating:891, as_of:"2026-10-27"}`.

### 4.6 Leaderboard (statistical leaderboards)
- **Fields:** `id uuid PK`; `scope_type` (`series|season|format|career|tournament`); `scope_ref_id uuid null`; `metric text` (`most_runs|most_wickets|highest_score|best_sr|most_sixes|best_econ|...`); `format_type null`; `rank int`; `player_id uuid null`; `team_id uuid null`; `value numeric(12,3)`; `supporting jsonb`; `computed_at`.
- **Unique:** `(scope_type, coalesce(scope_ref_id,'0'), metric, coalesce(format_type,'any'), rank)`.
- **Class:** DRV/CACHE.

### 4.7 PerformanceAggregate / FormTrend
- **Purpose:** "Recent form" (last N innings), rolling averages, trend.
- **Fields:** `id uuid PK`; `player_id uuid FK`; `format_type null`; `window_size int` (`5|10|all`); `metric text`; `value numeric`; `series jsonb` (per-innings values for sparkline); `computed_at`.
- **Unique:** `(player_id, coalesce(format_type,'any'), window_size, metric)`.
- **Class:** DRV/CACHE.

### 4.8 TournamentStandings (points table)
- **Fields:** `id uuid PK`; `series_id uuid not null FK`; `stage_id uuid null FK match_stage`; `team_id uuid not null FK`; `group_name text null`; `matches_played/won/lost/tied/drawn/no_result int`; `points int`; `nrr numeric(8,3)`; `form text` (e.g. `"WWLNR"`); `qualify_status text null`; `rank int`; `no_of_qual int null`; `revision int`; `computed_at`.
- **Unique:** `(series_id, coalesce(stage_id,'0'), team_id)`.
- **Class:** DRV/CACHE.

---

## 5. Optional fantasy scoring

### 5.1 FantasyRuleset
- **Fields:** `id uuid PK`; `name text`; `provider_scope text` (`generic|dream11_like|custom`); `description text`; `is_active bool`; `created_by uuid`.
- **Class:** SRC (config).

### 5.2 FantasyRulesetVersion
- **Fields:** `id uuid PK`; `ruleset_id uuid not null FK`; `version int not null`; `effective_from timestamptz`; `effective_to timestamptz null`; `format_scope enum_format_type null`; `is_frozen bool` (immutable once used).
- **Unique:** `(ruleset_id, version)`.
- **Class:** SRC. **Versioning:** versioned by design — this is the audit anchor.

### 5.3 FantasyPointRule
- **Fields:** `id uuid PK`; `ruleset_version_id uuid not null FK`; `event_type text` (`run|boundary_four|boundary_six|wicket|maiden_over|catch|stumping|runout|dot_ball|economy_bonus|strike_rate_bonus|playing_xi|duck|starting_xi|...`); `role_scope text null` (`batter|bowler|all_rounder|keeper|any`); `points numeric(6,2)`; `condition jsonb null` (e.g. `{min_runs:50}`); `cap_kind text null` (`none|per_match|per_innings`).
- **Unique:** `(ruleset_version_id, coalesce(role_scope,'any'), event_type, coalesce(condition_hash,''))`.
- **Class:** SRC (config).

### 5.4 PlayerMatchPerformance (fantasy)
- **Fields:** `id uuid PK`; `match_id uuid FK`; `player_id uuid FK`; `ruleset_version_id uuid FK`; `total_points numeric(8,2)`; `breakdown jsonb` (per-event points); `is_playing_xi bool`; `computed_at`; `revision`.
- **Unique:** `(match_id, player_id, ruleset_version_id)`.
- **Class:** DRV.

### 5.5 FantasyPointsLedger (immutable)
- **Fields:** `id uuid PK`; `match_id uuid`; `player_id uuid`; `ruleset_version_id uuid`; `delta numeric(8,2)`; `event_type text`; `reason text`; `created_at`; `created_by`.
- Append-only; the sum of deltas = `PlayerMatchPerformance.total_points`.
- **Class:** derived/audit.

### 5.6 FantasyPointAdjustment
- **Fields:** `id uuid PK`; `match_id uuid`; `player_id uuid`; `delta numeric(8,2)`; `reason text not null`; `adjusted_by uuid`; `created_at`.

### 5.7 FantasyCalculationAudit
- **Fields:** `id uuid PK`; `match_id uuid`; `ruleset_version_id uuid`; `input_digest text` (hash of the input stat snapshot); `output_digest text`; `computed_at`; `engine_version text`; `notes text`.
- Enables re-computation and dispute resolution.

---

## 6. Data operations

### 6.1 DataProvider
- **Fields:** `id uuid PK`; `name text not null` (unique); `code text` (unique); `base_url text`; `auth_kind text`; `credential_ref text null` (reference to secret store, **never** the secret); `supports_push bool`; `supports_ball_by_ball bool`; `supports_commentary bool`; `licensing_notes text`; `is_active bool`.
- **Class:** SRC. **Example:** `{name:"Entity Sport", code:"entitysport", supports_ball_by_ball:true}`.

### 6.2 ProviderEntityMap
- **Purpose:** Map internal entity ↔ provider IDs (players, teams, venues,
  series, matches) and across providers (Cricsheet ↔ Opta ↔ others).
- **Fields:** `id uuid PK`; `provider_id uuid FK data_provider`; `entity_type text` (`player|team|venue|series|match|competition|season`); `internal_id uuid not null`; `provider_entity_id text not null`; `provider_unique_name text null`; `confidence numeric(4,3) default 1.0`; `is_primary bool`; `created_at`; `verified bool`.
- **Unique:** `(provider_id, entity_type, provider_entity_id)`; `(entity_type, internal_id, provider_id)`.
- **Class:** SRC/metadata.
- **Example:** `{provider:"entitysport", entity_type:"player", internal_id:"…", provider_entity_id:"8095", confidence:1.0}`.

### 6.3 ImportJob
- **Fields:** `id uuid PK`; `provider_id uuid FK`; `job_type text` (`fixtures|match|scorecard|deliveries|stats|rankings|corrections|backfill`); `scope jsonb` (params); `status text` (`queued|running|succeeded|failed|partial`); `idempotency_key text`; `started_at`; `finished_at`; `records_read/written/skipped int`; `error text`; `created_by`.
- **Unique:** `idempotency_key`.
- **Class:** operational.

### 6.4 SyncCheckpoint
- **Fields:** `id uuid PK`; `provider_id uuid`; `scope_key text not null` (e.g. `match:147942`); `last_delivery_seq bigint null`; `last_provider_updated_at timestamptz null`; `last_run_at`; `cursor text null` (opaque provider cursor).
- **Unique:** `(provider_id, scope_key)`.
- **Class:** operational.

### 6.5 IngestionError (dead-letter)
- **Fields:** `id uuid PK`; `import_job_id uuid null FK`; `provider_id uuid`; `stage text` (`fetch|validate|resolve|persist|recalc|publish`); `payload jsonb`; `error_class text`; `error_detail text`; `attempt int`; `next_retry_at timestamptz null`; `resolved bool default false`; `created_at`.
- **Indexes:** `ix_ingestion_error_unresolved` (partial where `not resolved`).
- **Class:** operational.

### 6.6 ReconciliationJob
- **Fields:** `id uuid PK`; `scope_key text`; `expected_state jsonb`; `observed_state jsonb`; `differences jsonb`; `status text`; `created_at`; `resolved_at`.
- **Class:** operational.

### 6.7 DataCorrection
- **Purpose:** Record provider corrections to previously ingested data; support
  replay/recompute.
- **Fields:** `id uuid PK`; `provider_id uuid`; `entity_type text`; `entity_id uuid`; `provider_entity_id text null`; `field_path text`; `old_value jsonb`; `new_value jsonb`; `reason text`; `applied_by uuid null`; `applied_at`; `source_revision int null`; `target_revision int not null`.
- **Indexes:** `ix_correction_entity(entity_type, entity_id)`.
- **Class:** audit (append-only).

### 6.8 DataProvenance
- Per-row provenance is embedded as `source_provider_id`, `provider_updated_at`,
  `ingested_at`, `revision` columns on ingested tables. A central
  `record_provenance` table is provided for entities that aggregate multiple
  sources: `{id, entity_type, entity_id, field_path, provider_id, value_digest, observed_at, confidence}`.

### 6.9 AuditLog
- **Purpose:** Append-only audit trail of admin/ops actions (who did what, to
  which entity, before/after, when, from where).
- **Fields:** `id uuid PK`; `actor_id uuid null`; `actor_label text null`;
  `action text not null`; `entity_type text null`; `entity_id uuid null`;
  `before jsonb null`; `after jsonb null`; `ip inet null`;
  `created_at timestamptz not null default now()`.
- **Indexes:** `ix_audit_entity(entity_type, entity_id)`,
  `ix_audit_created(created_at DESC)`.
- **Class:** audit (append-only). Owned by the `administration` module.

---

## 7. Entity-relationship summary (cardinality)

```
Country 1─N Team 1─N MatchTeam N─1 Match
Country 1─N Player 1─N TeamMembership N─1 Team
Team 1─N Squad 1─N SquadMember N─1 Player
Competition 1─N Season 1─N Series 1─N Match
Match 1─N MatchPlayer N─1 Player
Match 1─1 MatchResult ; Match 1─N MatchAward N─1 Player
Match 1─N MatchEventLog
FormatCatalogue 1─N Match
Venue 1─N Ground ; Venue 1─N Match
Player 1─N PlayerMerge N─1 Player (canonical)
Match 1─N Innings 1─N Delivery N─1 Player (batter/nonstriker/bowler/fielders)
Innings 1─N Over 1─N Delivery
Innings 1─N BattingEntry/BowlingEntry/FallOfWicket/Partnership
Innings 1─N InningsEvent N─1 Player
Player 1─N PlayerMatchStat N─1 Match
Player 1─N PlayerCareerStat (scoped)
Series 1─N TournamentStandings N─1 Team
Match 1─N PlayerMatchPerformance N─1 FantasyRulesetVersion 1─N FantasyPointRule
```

## 8. Deliberate non-tables (avoid table-per-field)

| Displayed concept | Modelled as | Reason |
|---|---|---|
| Batter, NonStriker, Bowler, Fielder | FKs on `Delivery` | No independent identity/lifecycle |
| Current run rate, required run rate, run splits | Computed in scoring service / read model | Derived, cheap to compute |
| Overs-display `"10.3"` | Presentation of `legal_balls` | Formatting, not data |
| Batting/bowling innings | Views over `Innings` | Same row, different perspective |
| Target / follow-on | Columns on `Innings`/`Match` | 1:1 with innings/match |
| extras totals | Aggregate over `Delivery` | Derived |

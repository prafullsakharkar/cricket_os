# Phase B — Reference data (implementation plan)

**Date:** 2026-10-30 · **Branch:** `phase-b` (worktree
`~/.local/share/opencode/worktree/619363/phase-b`) · **Effort estimate:** 25 dev-days (roadmap).

## 1. Goal

Countries, governing bodies, venues, grounds, teams, players, competitions,
seasons, series, squads and match stages can be **stored, imported
idempotently, de-duplicated with steward review, and browsed through a
read-only cached public API**, on top of the provider identity-mapping
infrastructure (`DataProvider` + `ProviderEntityMap`) that Phases C/D build on.

Out of scope (later phases): matches/innings/deliveries, scoring persistence,
live ingestion, JWT/OAuth2, Redis response caching (Phase E), cross-entity
`/search` (Phase F), rankings, fantasy, realtime. `FormatCatalogue` **is** in
scope (moved from `matches` → `competitions`, see Decisions D3).

## 2. Sources of truth & conflict rules

Read first (already read in full this session): ROADMAP (Phase B + cross-phase
discipline), DATABASE_SCHEMA (§1, §3.1–3.4, §3.8, §4.2–4.3, ER diagram),
DOMAIN_MODEL (§0, §1.15 PlayerMerge, §6 provider mapping/AuditLog),
ARCHITECTURE (module catalog §2, six layers §4, directory §5, events §5),
API_SPECIFICATION (§1 conventions, §2.2–2.4 matrix, §3.9 caching),
DATA_INGESTION (§1 matrix, §2 ProviderAdapter protocol),
SECURITY_AND_RELIABILITY (§1 roles, §4 audit), RISKS_AND_ASSUMPTIONS (RSK-08),
TESTING_STRATEGY (§1–§5, §9, §13.1, §16), DB_CONVENTIONS, MIGRATION_WORKFLOW,
README.

- **DATABASE_SCHEMA.md wins on table structure** (columns, nullability,
  constraint/index names, types).
- **ARCHITECTURE.md wins on module boundaries** (app ownership, six layers).
- Every conflict we resolve is listed in **§5 Decisions** and in the final
  report item (d).

## 3. Global constraints (apply to every task)

1. **Phase A gates stay on** (ruff, mypy strict, import-linter, coverage).
   Extend import-linter contracts (task 2.4) — do not weaken existing ones.
2. **Exact DDL match** for every table: constraint names
   (`ux_*`, `ix_*`, `ck_*`), partial unique indexes (condition), check
   constraints `ck_<table>_<column>_valid`, `GinIndex(opclasses=["gin_trgm_ops"])`,
   the persisted `name_key` GeneratedField (IMMUTABLE expression), and the
   open-membership index `coalesce(left_at, 'infinity')`.
3. `source_provider_id` is a **plain `UUIDField`** (matching the DDL `uuid`
   column), **not** an FK and not `ProvenanceModel.source_provider_id`
   (TextField). Domain apps never import `providers.models` at the *model*
   layer; they only receive `DataProvider` instances/uuids from services.
4. **Six layers:** selectors never import services; services are
   transactional + idempotent; upserts keyed by `(provider, provider_entity_id)`.
5. **Public surface is read-only.** No write endpoints. Provider ids and
   internal error detail never leak through the API. UUIDs only in paths.
6. Every **migration is reversible** (data migrations included). Exceptions
   documented inline with a reason.
7. **No network in tests.** Cricsheet fixtures are committed golden CSVs.
8. Coverage: new apps ≥ 90% (main gate extended); `identity_scoring.py` and
   `merge.py` at **100% branch** (separate gate). New apps in
   `ci/schema_drift_scope.txt` in the same change as their migration.
9. Every table the DDL documents for Phase B lands in `schema_drift_scope.txt`
   and the `schema-ddl`/`migration-drift` CI jobs must stay green.
10. Feature-flag / commit discipline: **one reviewable commit per task**, never
    a giant blob.

## 4. Commit groups (8) — TDD, each green before the next

Each group ends with `make check` + the relevant migration/schema gates green.
Write the failing test first, then the implementation.

---

### GROUP 1 — `core` additions: event bus, HTTP cache helpers, drf-spectacular

**Files**
- `apps/core/events.py` — NEW
- `apps/core/http.py` — NEW
- `apps/core/tests/` (new package) `test_events.py`, `test_http.py`
- `config/settings/base.py`, `config/settings/dev.py`, `config/settings/test.py`,
  `config/settings/prod.py`
- `config/urls.py`
- `pyproject.toml`, `uv.lock` (add `drf-spectacular`; add `schemathesis` + `jsonschema` to dev group)
- `Makefile` (no change yet — gate changes land in group 8)

**Task 1.1 — `core/events.py` (TDD)**
- `subscribe(topic: str, handler: Callable[[Any], None]) -> None` — append to a
  module-level `dict[str, list]`.
- `publish(topic: str, payload: Any, *, on_commit: bool = True) -> None`:
  - default: `transaction.on_commit(lambda: _dispatch(topic, payload))`;
  - `on_commit=False`: `_dispatch(topic, payload)` synchronously (audit path).
  - `_dispatch` calls every handler, catching and logging nothing (let
    exceptions propagate — audit must fail the transaction).
- `clear_subscriptions()` for tests; `TOPIC_PLAYER_MERGED = "player.merged"`,
  `TOPIC_PROVIDER_MAP_VERIFIED = "provider_map.verified"`,
  `TOPIC_DUPLICATE_REVIEWED = "player_duplicate.reviewed"` constants.
- Tests: subscribe+publish delivery; handler receives payload; multiple
  handlers; `on_commit=True` fires **after commit** (assert inside/outside an
  `atomic` block using a list-append side effect); **rollback test**: publish
  inside `atomic()` then raise → handler must NOT have fired; `on_commit=False`
  fires synchronously even inside an uncommitted transaction.

**Task 1.2 — `core/http.py` (TDD)**
- `compute_etag(payload: bytes | str) -> str` — `sha256` hexdigest wrapped in
  quotes (`'"<hex>"'`, strong).
- `apply_cache_policy(response, *, max_age: int, swr: int | None = None,
  etag: str | None = None) -> Response` — sets `Cache-Control:
  public, max-age=<max_age>` (+ `, stale-while-revalidate=<swr>` when swr),
  `ETag` header; if the request carried `If-None-Match` equal to the strong etag
  → return `304` (empty body) **preserving** `Cache-Control`/`ETag`.
- `CacheControlMixin` (DRF view mixin): class attrs `cache_max_age`,
  `cache_swr`; overrides `finalize_response` to render, then apply the policy
  from the serialized payload (`response.rendered_content`), skipping 3xx/4xx/5xx.
  Per-view TTL config = the class attrs (no hard-coded TTLs in http.py).
- Tests: header shape; strong etag changes when payload changes; `If-None-Match`
  → 304 with preserved cache headers; no 304 on mismatch; 404/301 not cached.

**Task 1.3 — drf-spectacular**
- Add `drf-spectacular` to runtime deps; `INSTALLED_APPS += drf_spectacular`.
- `SPECTACULAR_SETTINGS` in base: title "Cricket OS API", version "v1",
  `COMPONENT_SPLIT_REQUEST=True`, `SERVE_INCLUDE_SCHEMA=False`.
- `config/urls.py`: `path("api/v1/schema", ...)` gated by a settings flag
  `ENABLE_API_SCHEMA` (True in dev+test, False in prod — set in each settings
  file). Justification for the dependency (report item): the API spec mandates
  contract tests against the OpenAPI schema (§5 testing) — schemathesis needs a
  schema document; drf-spectacular is the standard generator for DRF.
- Test: schema endpoint reachable under test settings and returns a JSON object
  with `openapi` key; `config.settings.prod` sets `ENABLE_API_SCHEMA=False`.

**Task 1.4 — schemathesis dev dep + smoke**
- Add `schemathesis` (pin the newest version that installs on Python 3.14; if
  none does, record it and use `jsonschema`-based schema validation as the
  fallback contract mechanism — see Decisions D8).
- Smoke: load the generated schema via `schemathesis.from_path`/`from_wsgi` in a
  single test and assert it parses (full endpoint tests land in group 8).

**Group 1 verification:** `make check`; `pytest tests/core/` (new) green.

---

### GROUP 2 — Models, migrations, seeds, drift scope

Apps created here: `venues`, `teams`, `players`, `competitions`, `providers`,
`administration`. Register each in `config/settings/base.py` `LOCAL_APPS`.
Each app gets `apps.py` with `default_auto_field = "django.db.models.UUIDField"`.

**Model rules (apply everywhere)**
- `id` from `UUIDPrimaryKeyModel`; tables named exactly as the DDL.
- **Only the columns the DDL lists.** Do **not** add `created_at`/`updated_at`
  unless the DDL has them (D1). `source_provider_id` is `UUIDField(null=True)`.
- Enum-like text columns → `CharField(choices=...)` + `CheckConstraint(
  condition=Q(<field>__in=<labels>), name="ck_<table>_<field>_valid")`.
- Every index/constraint explicitly named per DDL (or `ux_/ix_/ck_` convention
  for inline DDL constraints, e.g. `data_provider.code` → `ux_data_provider_code`).
- `Meta.ordering` never set on models (ordering lives in selectors).

**Task 2.1 — `venues` app**
- `Country` (`country`): `iso_code` CharField(2, null, blank) + validator
  (uppercase ISO-3166 alpha-2 regex), `iso3` CharField(3, null, blank) +
  validator, `name` TextField NOT NULL, `short_name`, `flag_image_url`,
  `is_active` default True, `source_provider_id` UUID null.
  `UniqueConstraint(iso_code, name="ux_country_iso")`,
  `UniqueConstraint(name, name="ux_country_name")`.
- `GoverningBody` (`governing_body`): `name` NOT NULL, `abbreviation`,
  `country` FK→Country (null), `parent` FK→self (null),
  `type` + `ck_governing_body_type_valid` (`international,regional,national,
  franchise_league`), `ux_gov_name` unique name.
- `Venue` (`venue`): `name` NOT NULL, `city`, `country` FK, `timezone`
  default "UTC" + `validate_iana_timezone` validator (`zoneinfo.ZoneInfo`,
  raise `ValidationError` on `ZoneInfoNotFoundError`), `latitude` Decimal(9,6),
  `longitude` Decimal(9,6), `capacity` PositiveInteger? (plain `integer` — DDL
  has no check, so `IntegerField`), `is_neutral` default False,
  `provider_venue_id` TextField, `source_provider_id` UUID null.
  `UniqueConstraint(expressions=["name", Coalesce("city", Value(""))],
  name="ux_venue_name_city")`, `Index(fields=["country_id"],
  name="ix_venue_country")`, `Index(fields=["source_provider_id",
  "provider_venue_id"], name="ix_venue_provider")`.
- `Ground` (`ground`): `venue` FK NOT NULL, `name` NOT NULL, `notes`,
  `ux_ground` unique (venue, name).
- Validators in `apps/venues/validators.py` (IANA timezone, ISO codes) — unit
  tested directly.
- Tests: constraint enforcement (unique names, venue name+city with NULL city
  allowed twice but same (name,city) rejected), timezone validator rejects
  `"Mars/Olympus"` and accepts `"Asia/Kolkata"`, ISO validator rejects `"IN1"`.

**Task 2.2 — `teams` app** (no imports from `apps.players` — lazy string FKs)
- `Team` (`team`): `name` NOT NULL, `short_name`, `abbreviation`,
  `team_type` + `ck_team_team_type_valid` (TeamType labels), `country` FK,
  `governing_body` FK, `home_venue` FK, `gender` CharField(1) default "M" +
  `ck_team_gender_valid` (`M,W,X`), `is_international` default False,
  `logo_url`, `provider_team_id` TextField null, `source_provider_id` UUID null,
  `is_active` default True, `deleted_at` DateTimeField null.
  `UniqueConstraint(fields=["source_provider_id","provider_team_id"],
  condition=Q(provider_team_id__isnull=False), name="ux_team_provider")`,
  `Index(fields=["team_type"], name="ix_team_type")`,
  `GinIndex(fields=["name"], opclasses=["gin_trgm_ops"],
  name="ix_team_name_trgm")`.
- `TeamMembership` (`team_membership`): `team` FK, `player` FK
  (`"players.Player"`, lazy), `joined_at` DateField, `left_at` DateField,
  `membership_type` default "squad" + `ck_team_membership_membership_type_valid`,
  `is_captain`, `is_primary`, `source_provider_id` UUID null.
  `UniqueConstraint(expressions=["team_id","player_id",
  Coalesce("left_at", Value("infinity", output_field=models.DateField()))],
  name="ux_membership_open")`, `Index(fields=["player_id"],
  name="ix_membership_player")`.
- `Squad` (`squad`): `team` FK NOT NULL, `series` FK
  (`"competitions.Series"`, lazy), `season` FK (`"competitions.Season"`, lazy),
  `name`, `announced_at`, `source_provider_id` UUID null.
  `Index(team_id, name="ix_squad_team")`, `Index(series_id,
  name="ix_squad_series")`.
- `SquadMember` (`squad_member`): `squad` FK NOT NULL, `player` FK
  (`"players.Player"`), `role`, `is_captain`, `is_keeper`, `is_substitute`,
  `status` null + `ck_squad_member_status_valid` (`announced,withdrawn,
  replaced`), `ux_squad_member` unique (squad, player).
- Tests (integration, real DB): **membership temporality** — same
  (team, player) with `left_at=NULL` twice rejected; with distinct `left_at`
  allowed; **re-join after leaving** allowed (old row `left_at` set, new row
  `left_at=NULL`); partial unique `ux_team_provider` rejects a second row with
  the same provider id but allows NULLs; soft delete visible in raw ORM,
  excluded by selectors (task 3.1).

**Task 2.3 — `players` app** (no imports from `apps.teams.models`)
- `Player` (`player`): `full_name` NOT NULL, `known_as`, `unique_name`,
  `date_of_birth` DateField, `date_of_death`, `birth_country` FK, `birth_place`,
  `gender` CharField(1) default "M" + `ck_player_gender_valid`,
  `height_cm` IntegerField, `batting_style` + `ck_player_batting_style_valid`,
  `bowling_style` + `ck_player_bowl_style_valid`, `primary_role` +
  `ck_player_primary_role_valid`, `is_wicketkeeper` default False,
  `international_team` FK (`"teams.Team"` lazy), `face_image_url`, `bio`,
  `provider_player_id` TextField null, `source_provider_id` UUID null,
  `is_active` default True, `deleted_at` null,
  **`name_key` persisted GeneratedField**:
  ```python
  models.GeneratedField(
      expression=Concat(
          Lower(F("full_name")),
          Value("|"),
          Coalesce(Cast(F("date_of_birth") - Value(datetime.date(1, 1, 1)),
                         models.TextField()), Value("")),
      ),
      output_field=models.TextField(),
      db_persist=True,
  )
  ```
  `UniqueConstraint(fields=["source_provider_id","provider_player_id"],
  condition=Q(provider_player_id__isnull=False), name="ux_player_provider")`,
  `Index(fields=["date_of_birth"], name="ix_player_dob")`,
  `Index(fields=["international_team_id"], name="ix_player_intl_team")`,
  `GinIndex(fields=["full_name"], opclasses=["gin_trgm_ops"],
  name="ix_player_name_trgm")`.
- `PlayerMerge` (`player_merge`): `canonical_player` FK, `merged_player` FK,
  `reason`, `merged_by` UUID null, `merged_at` DateTimeField(default=timezone.now),
  `ux_player_merge` unique (merged_player), `ck_player_merge_distinct`
  (`canonical_player_id <> merged_player_id`), `Index(fields=["canonical_player_id"],
  name="ix_player_merge_canonical")`. Add `Meta.permissions =
  [("merge_players", "Can merge duplicate players")]`.
- **NEW** `PlayerDuplicateCandidate` (`player_duplicate_candidate`) — add DDL to
  DATABASE_SCHEMA.md §3.3 + DOMAIN_MODEL.md §1.x + ER diagram **in this same
  change** (Task 2.7):
  ```sql
  CREATE TABLE cricket.player_duplicate_candidate (
    id uuid PRIMARY KEY,
    candidate_player_id uuid NOT NULL REFERENCES cricket.player(id),
    canonical_player_id uuid NOT NULL REFERENCES cricket.player(id),
    score numeric(4,3) NOT NULL,
    feature_breakdown jsonb NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','merged')),
    reviewed_by uuid,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
  );
  CREATE UNIQUE INDEX ux_dup_candidate ON cricket.player_duplicate_candidate
    (candidate_player_id, canonical_player_id);
  CREATE INDEX ix_dup_candidate_status ON cricket.player_duplicate_candidate
    (status, created_at DESC);
  CREATE INDEX ix_dup_candidate_canonical ON cricket.player_duplicate_candidate
    (canonical_player_id);
  ```
  Model mirrors it; `ck_player_duplicate_candidate_status_valid` on `status`.
- Tests: generated `name_key` is persisted, immutable-friendly (insert row with
  full_name/DoB, assert `name_key == lower(full_name)|<days>`); duplicate
  candidate unique constraint; merge distinct check.

**Task 2.4 — `competitions` app**
- `Competition` (`competition`): `name` NOT NULL, `short_name`,
  `competition_type` + `ck_competition_competition_type_valid`,
  `governing_body` FK, `format_type` + `ck_competition_format_type_valid`,
  `is_international` default False, `gender` CharField(1) default "M"
  (**no** check — DDL has none), `provider_competition_id` TextField null,
  `source_provider_id` UUID null, `ux_competition_name` unique name,
  `UniqueConstraint(fields=["source_provider_id","provider_competition_id"],
  condition=Q(provider_competition_id__isnull=False),
  name="ux_competition_provider")`.
- `Season` (`season`): `competition` FK, `label` NOT NULL, `start_date`,
  `end_date`, `ux_season` unique (competition, label).
- `Series` (`series`): `name` NOT NULL, `short_name`, `competition` FK,
  `season` FK, `series_type` default "bilateral" +
  `ck_series_series_type_valid` (`bilateral,triangular,tournament,league,
  icc_event`), `start_date`, `end_date`, `host_country` FK, `is_tournament`
  default False, `points_system` JSONField, `provider_series_id` TextField null,
  `source_provider_id` UUID null, `ux_series_provider` (partial, like team),
  `Index(fields=["start_date","end_date"], name="ix_series_dates")`.
- `MatchStage` (`match_stage`): `series` FK NOT NULL, `name` NOT NULL,
  `stage_type` + `ck_match_stage_stage_type_valid`
  (`group,super_league,quarter_final,semi_final,final,playoff,league`),
  `group_name`, `sequence` IntegerField, `ux_stage` unique (series, name).
- `FormatCatalogue` (`format_catalogue`, **in `competitions`**, see D3):
  `format` CharField(primary_key=True, max_length=20) +
  `ck_format_catalogue_format_valid`, `max_innings` SmallIntegerField +
  `ck_format_catalogue_max_innings_valid` (2/4), `overs_limit` IntegerField null,
  `balls_per_over` SmallIntegerField default 6, `allows_declaration`,
  `allows_follow_on`, `allows_draw`, `allows_super_over` (all default False
  except `allows_super_over` True).
- Tests: competition/season/series constraints; series provider partial unique;
  stage unique; format_catalogue check constraints.

**Task 2.5 — `providers` app** (`providers/registry.py`,
`providers/base.py`, models)
- `DataProvider` (`data_provider`): `name` NOT NULL, `code` NOT NULL +
  `ux_data_provider_code` unique, `base_url`, `auth_kind`,
  `credential_ref` TextField null (**a reference only, never a secret**),
  `supports_push`, `supports_ball_by_ball`, `supports_commentary`,
  `licensing_notes`, `is_active` default True.
- `ProviderEntityMap` (`provider_entity_map`): `provider` FK, `entity_type`
  + `ck_provider_entity_map_entity_type_valid`
  (`player,team,venue,series,match,competition,season`), `internal_id`
  `UUIDField` (**plain, polymorphic — no FK**), `provider_entity_id` TextField,
  `provider_unique_name`, `confidence` DecimalField(4,3) default 1.0,
  `is_primary` default False, `verified` default False, `created_at`
  auto_now_add. `UniqueConstraint(fields=["provider_id","entity_type",
  "provider_entity_id"], name="ux_pem")`, `Index(fields=["entity_type",
  "internal_id"], name="ix_pem_internal")`.
- `providers/registry.py`: `register_adapter(code, adapter_factory)`,
  `get_adapter(code)` (raises `ProviderNotRegistered`), `list_providers()` —
  stub registry, no adapters yet.
- `providers/base.py`: `ProviderAdapter` **Protocol** copied from
  DATA_INGESTION §2 (methods `list_fixtures`, `get_match`, `get_scorecard`,
  `iter_deliveries`, `get_commentary`, `get_rankings`, `normalize`,
  `capabilities`) + `ProviderCapabilities` dataclass. No implementations.
- Tests: provider code unique; map unique `ux_pem` rejects duplicate
  (provider, entity_type, provider_entity_id) but allows same provider id for a
  different entity_type; map check constraint rejects `entity_type="umpire"`;
  registry get/register/unknown-code.

**Task 2.6 — `administration` app** (AuditLog only)
- `AuditLog` (`audit_log`): `actor_id` UUID null, `actor_label` TextField null,
  `action` NOT NULL, `entity_type` TextField null, `entity_id` UUID null,
  `before` JSONField null, `after` JSONField null, `ip`
  GenericIPAddressField(null=True), `created_at` auto_now_add.
  `Index(fields=["entity_type","entity_id"], name="ix_audit_entity")`,
  `Index(fields=["-created_at"], name="ix_audit_created")`.
- Tests: row creation; index names; `ip` accepts v4/v6; no other models in the
  app (import-linter contract in task 2.8).

**Task 2.7 — seeds (data migrations, reversible)**
- `venues` data migration `seed_countries_governing_bodies`: ICC + the 12 ICC
  full-member boards (ECB, CA, BCCI, PCB, NZC, CSA, SLC, BCB, ACB, CWI, ZC,
  Cricket Ireland) and the countries they reference (England, Australia, India,
  Pakistan, New Zealand, South Africa, Sri Lanka, Bangladesh, Afghanistan,
  Zimbabwe, Ireland; CWI → country NULL). Reverse deletes exactly these rows
  (by name). Idempotent (`get_or_create`-style on unique fields).
- `competitions` data migration `seed_format_catalogue`: the 9 DDL rows verbatim
  (TEST 4/NULL/6/t,t,t,f; FIRST_CLASS same; ODI 2/50/6/f,f,f,t; LIST_A same;
  T20I 2/20/6/f,f,f,t; T20 same; T10 2/10/6/f,f,f,t; THE_HUNDRED 2/20/5/f,f,f,t;
  OTHER 2/NULL/6/f,f,f,t). Reverse deletes the 9 rows.
- `providers` data migration `seed_cricsheet_provider`:
  code="cricsheet", name="Cricsheet", base_url="https://cricsheet.org",
  supports_ball_by_ball=True, licensing_notes = verified text
  (**2026-10-30 cricsheet.org/licensing**: "Cricsheet data is licensed under 2
  licenses: most data under the Open Data Commons Attribution License (ODC-By);
  XML match data under the Creative Commons Attribution 4.0 International
  (CC-BY 4.0). Both require attribution."). Reverse deletes it.
- **Presets-vs-seed agreement test** (`tests/competitions/test_seed_presets.py`,
  NOT skipped — presets exist in `apps/scoring/config.py`): for each of
  TEST/ODI/T20I/T20/THE_HUNDRED, compare the `format_catalogue` row with the
  matching `RuleConfig` preset (`_config_from_catalogue` mapping:
  Format.TEST→ICC_TEST_2024, ODI→ICC_ODI_2024, T20I→ICC_T20I_2024,
  T20→ICC_T20I_2024, THE_HUNDRED→THE_HUNDRED_RULES) on `max_innings`,
  `overs_limit`, `balls_per_over`, `allows_declaration`, `allows_follow_on`,
  `allows_draw`, `allows_super_over`. If a preset disagrees, the seed (DDL) is
  authoritative — fix the test expectation or the preset, and record it.

**Task 2.8 — drift scope + import-linter + migration tests**
- `ci/schema_drift_scope.txt`: add all 19 tables
  (`country governing_body venue ground format_catalogue team player
  team_membership player_merge player_duplicate_candidate competition season
  series squad squad_member match_stage data_provider provider_entity_map
  audit_log`).
- Run the migration-drift workflow locally (commands in §7) and **fix real
  drift**; allowlist only genuinely accepted differences, each with a comment
  (e.g. Django's `DEFAULT now()` vs reference `now()` defaults are not
  compared — expect few entries, possibly none).
- `pyproject.toml` import-linter additions:
  ```toml
  [[tool.importlinter.contracts]]
  name = "selectors must not import services"
  type = "forbidden"
  source_modules = ["apps.*.selectors"]
  forbidden_modules = ["apps.*.services"]

  [[tool.importlinter.contracts]]
  name = "domain apps never import administration/ingestion/realtime"
  type = "forbidden"
  source_modules = ["apps.venues", "apps.teams", "apps.players", "apps.competitions", "apps.providers"]
  forbidden_modules = ["apps.administration", "apps.ingestion", "apps.realtime"]

  [[tool.importlinter.contracts]]
  name = "api must not import provider adapters"
  type = "forbidden"
  source_modules = ["apps.*.api"]
  forbidden_modules = ["apps.providers.adapters", "apps.ingestion", "apps.realtime"]

  [[tool.importlinter.contracts]]
  name = "teams and players model modules never import each other"
  type = "forbidden"
  source_modules = ["apps.teams.models"]
  forbidden_modules = ["apps.players.models"]
  ```
  plus the mirror (players.models → teams.models).
- Migration tests (`tests/test_migrations.py` additions + per-app): every
  migration forward/backward on an empty DB and on a DB populated by the
  importer fixture (group 7); `makemigrations --check --dry-run` clean.
- `tests/test_enums.py` already guards core enums vs the doc — keep green.

**Group 2 verification:** `make check`; `make migrate` clean on a fresh DB;
migration-drift compare shows **no drift** across all 19 scoped tables;
schema-ddl job applies the updated doc DDL (incl. `player_duplicate_candidate`).

---

### GROUP 3 — Selectors, services, identity resolution

**Task 3.1 — selectors** (`apps/<app>/selectors/` packages; no services imports)
- `venues/selectors/venues.py`: `list_countries()`, `get_country(pk)`,
  `list_venues(**filters)`, `get_venue(pk)`, `list_governing_bodies()`.
  Default-exclude `is_active=False` / `deleted_at__isnull=False` where the
  model has them; `select_related("country")` etc.
- `teams/selectors/teams.py`: `list_teams(*, q, category, country, ordering)`
  (trigram `q` via `TrigramSimilarity` filter+rank), `get_team(pk)` (prefetch
  memberships). `teams/selectors/memberships.py`: `list_players_of_team(team_id,
  *, as_of=None)` (temporality: `joined_at <= as_of < left_at`, default
  open-ended).
- `players/selectors/players.py`: `list_players(*, q, team, country, role,
  ordering)` (trigram-ranked when `q`, cursor-ready otherwise), `get_player(pk)`,
  `list_duplicate_candidates(*, status="open")` (order by score desc).
- `competitions/selectors/competitions.py`: `list_competitions(**filters)`,
  `get_competition(pk)` (prefetch seasons), `list_series(*, year, category,
  type, ordering)`, `get_series(pk)` (prefetch season/competition),
  `get_format(format)`.
- `providers/selectors/providers.py`: `get_provider(code)`,
  `get_or_create_provider(code, **defaults)` (idempotent).
- Tests: soft-deleted/inactive rows excluded; `q` trigram search returns the
  ranked match; team membership temporality selector (active on date X, re-join
  yields both rows with correct `as_of` semantics); N+1-free (assertNumQueries).

**Task 3.2 — upsert services** (six-layer: no selector imports these)
- Each of `teams/services/teams.py` (`upsert_team(provider, provider_entity_id,
  *, name, ...) -> Team`), `venues/services/venues.py` (`upsert_venue`),
  `players/services/players.py` (`upsert_player`), `competitions/services/
  competitions.py` (`upsert_competition`, `upsert_series`), all keyed by
  `(provider, provider_entity_id)` **via the provider map** — never by name
  alone: exact map hit → return; else create/update the entity + map row
  (`verified=False`), inside `transaction.atomic()`, idempotent on re-run.
- `players/services/players.py` also: `mark_inactive(player)`,
  `verify_mapping(map_row, *, actor)` → sets `verified=True` and publishes
  `provider_map.verified` (on_commit=False, audit path).
- Tests: run-twice idempotency; map row created with the right provider; update
  path updates fields; name-based upsert without a map never matches a different
  entity.

**Task 3.3 — `players/identity_scoring.py` (PURE — no DB, no Django imports
beyond dataclasses/typing)**
- `normalize_name(value) -> str`: Unicode NFKD → strip combining marks →
  `casefold()` → remove punctuation → collapse whitespace.
- `score_pair(a: PlayerFeatures, b: PlayerFeatures) -> ScoreResult` where
  `PlayerFeatures` is a frozen dataclass (`full_name`, `date_of_birth`,
  `birth_country`, `gender`, `team_ids: frozenset`) and `ScoreResult` carries
  `score: float` + `breakdown: dict[str, Any]`.
- **Weight table (documented in module docstring + IDENTITY_RESOLUTION.md):**
  name 0.35, date_of_birth 0.25, birth_country 0.15, gender 0.10,
  shared_teams 0.15 (sum 1.0).
- Name: normalized-equal → 1.0; initials-vs-full ("V Kohli" vs "Virat Kohli":
  every single-letter token of the short name equals the first letter of a token
  of the long name, and multi-letter tokens match normalized) → 1.0; token
  overlap ratio otherwise.
- DoB: both equal → 1.0; both present and **different → 0.0 AND caps the total
  score at 0.25** ("DoB mismatch caps the score low"); one/both missing → 0.5.
- Birth country: equal → 1.0; either missing → 0.5; different → 0.0.
- Gender: equal → 1.0; either unknown → 0.5; different → 0.0.
- Shared teams: `2 * |A∩B| / (|A|+|B|)` (0 when both empty).
- Total = weighted sum, then clamped to [0, 1]. Breakdown lists each feature's
  contribution, raw values, and the cap applied.
- Property tests (`tests/players/test_identity_scoring_properties.py`,
  hypothesis): **symmetry** (`score_pair(a,b) == score_pair(b,a)`), **bounds**
  (`0 <= score <= 1`), **determinism** (identical inputs → identical
  `ScoreResult`). Plus unit tests for each feature incl. initials, NFKD
  ("é" vs "e"), DoB mismatch cap.

**Task 3.4 — `players/services/identity_resolution.py` + generic
`providers/services.py` (see Decisions D4)**
- `providers/services.py`:
  - `register_entity_resolver(entity_type, resolver)` — resolver Protocol:
    `match(attributes) -> list[Candidate]` (`Candidate(internal_id, score,
    breakdown)`) and `create_stub(provider_entity_id, attributes) -> UUID`.
  - `resolve(provider, entity_type, provider_entity_id, attributes) -> UUID`:
    1. exact `ProviderEntityMap` hit → return `internal_id`;
    2. `resolver.match(attributes)`;
    3. unique candidate with `score >= settings.IDENTITY_MATCH_THRESHOLD`
       (default 0.95) → create map row (`verified=False`) to it, return it;
    4. else `resolver.create_stub(...)` + map row (`verified=False`) and return
       the stub id. **Never auto-merge.**
    - Concurrency: whole body in `transaction.atomic()`; on
      `IntegrityError` from `ux_pem` (lost race) roll back and re-read the
      winning row; map lookups use `select_for_update()` where the decision
      mutates. Idempotent by construction.
- `players/services/identity_resolution.py`:
  - `PlayerResolver` registered for `entity_type="player"` in
    `players/apps.py.ready()`.
  - `match(attributes)`: query candidate players (trigram name similarity
    filter or exact normalized-name, plus DoB/gender/country filters), score
    each with `identity_scoring.score_pair`, return candidates sorted desc.
  - `create_stub(...)`: create `Player` (is_active=True) + `ProviderEntityMap`
    row; then for every candidate with `score >= settings.
    DUPLICATE_CANDIDATE_THRESHOLD` (default 0.60) create a
    `PlayerDuplicateCandidate(candidate, canonical, score, breakdown,
    status="open")` (`get_or_create`, unique on the pair).
- Team/venue/series/competition resolvers (`teams/services/teams.py` etc.):
  normalised-name + qualifier matching (team: name+country; venue: name+city;
  series: name+season year; competition: name), each registering in `ready()`.
- **Concurrent resolve test** (`@pytest.mark.django_db(transaction=True)`):
  commit a provider + attributes, then two threads (barrier-synchronised) both
  call `resolve(...)`; assert exactly **one** Player and **one** map row exist.
  Also test exact-map-hit, high-confidence-link, stub+duplicate-candidate, and
  never-auto-merge (a 0.90 candidate does NOT get merged or linked).

**Group 3 verification:** `make check`; concurrent test passes repeatedly;
`identity_scoring.py` branch coverage 100% (local run with `--cov-branch`).

---

### GROUP 4 — Merge, audit, events wiring

**Files:** `players/services/merge.py`, `administration/handlers.py`,
`administration/apps.py`, `players/apps.py`, `teams/apps.py`,
`competitions/apps.py`, `venues/apps.py`, `providers/apps.py`,
`tests/players/test_merge.py`, `tests/administration/test_audit.py`,
`tests/dummy_app/` (test-only app).

**Task 4.1 — `register_player_reference(model, field)` registry + merge**
- `players/services/merge.py`:
  - `register_player_reference(model: type[Model], field_name: str)` — module
    registry of `(model, field_name)`; Phase B registers `TeamMembership.player`
    and `SquadMember.player` (from `teams.apps.ready()`); a **test-only app**
    (`tests/dummy_app` with one model `DummyPlayerRef.player` FK, hand-written
    migration, INSTALLED_APPS only in `config/settings/test.py`) registers in
    its own `ready()` to prove extensibility.
  - `merge_players(winner: Player, loser: Player, *, actor, reason) -> PlayerMerge`:
    - resolve redirect chains: follow `PlayerMerge` from winner and loser to
      their canonical players first ("merging into a player that was itself
      merged follows the redirect");
    - refuse `winner == loser` (`Conflict`), refuse chains that create cycles
      (canonical(loser) == winner after resolution, or a would-be cycle);
    - one `transaction.atomic()`:
      1. re-point `ProviderEntityMap.internal_id` loser→winner;
      2. re-point every registered reference (loser.id → winner.id) and any
         `PlayerMerge.canonical_player_id` rows pointing at loser;
      3. write `PlayerMerge(canonical_player_id=winner, merged_player_id=loser,
         reason, merged_by=actor-if-uuid, merged_at=now)`;
      4. loser.is_active=False, loser.deleted_at=now, save;
      5. `publish(TOPIC_PLAYER_MERGED, {...}, on_commit=False)` (audit
         atomicity — see Task 4.2);
    - **idempotent on retry**: if a `PlayerMerge` row already exists for the
      loser, return it (no-op) after resolving;
    - **no unmerge** — documented in module docstring + IDENTITY_RESOLUTION.md.
- Tests: happy path re-points map rows + memberships + squad members + the dummy
  ref; `PlayerMerge` row written; loser inactive/deleted; **idempotent** (call
  twice → one PlayerMerge, no double re-point); `winner == loser` refused;
  cycle refused; redirect-chain resolution (A→B merged, then merge B→C re-points
  A's map rows to C); **failed merge writes no audit row** (force a failure
  after the publish — e.g. a registered reference that violates a constraint —
  assert transaction rolls back and no AuditLog row); merge of already-merged
  loser is a no-op.

**Task 4.2 — audit subscriber + wiring**
- `administration/handlers.py`:
  - `on_player_merged(payload)` → `AuditLog(action=TOPIC_PLAYER_MERGED,
    entity_type="player", entity_id=canonical, after={"canonical_player_id":…,
    "merged_player_id":…}, actor_label=payload["actor"])`.
  - `on_provider_map_verified(payload)`, `on_duplicate_reviewed(payload)`.
- `administration/apps.py.ready()`: `subscribe(TOPIC_PLAYER_MERGED,
  on_player_merged)` etc.
- `players/apps.py.ready()`: register player entity resolver (group 3) — keep
  `ready()` idempotent (guard with `getattr(self, "_ready_done", False)`).
- Tests: merge → exactly one audit row, **in the same transaction**
  (`on_commit=False`: audit row visible inside the atomic block, absent after a
  rollback); failed merge → zero audit rows; provider-map verification →
  audit row; import-linter proves `players` never imports `administration`.

**Task 4.3 — event-bus wiring regression**
- `core/events.py` handlers registry cleared between tests (autouse fixture in
  `tests/conftest.py`: `events.clear_subscriptions()`).
- Test that `AppConfig.ready()` subscriptions are idempotent across multiple
  `ready()` calls (no duplicate audit rows).

**Group 4 verification:** `make check`; `tests/players/test_merge.py` and
`tests/administration/test_audit.py` green; import-linter green.

---

### GROUP 5 — Steward admin + roles

**Files:** `apps/*/admin.py` (all six apps), `apps/core/admin.py` (groups? no —
see below), `tests/admin/test_admin.py`, `tests/admin/test_permissions.py`,
data migration for Groups (in `administration`).

**Task 5.1 — admin classes (every reference model)**
- `CountryAdmin`, `GoverningBodyAdmin`, `VenueAdmin` (search `name`, city),
  `GroundAdmin`, `TeamAdmin` (search with **trigram** `get_search_results`
  override ranked by `TrigramSimilarity`; list_filter team_type/country/
  is_active; `ProviderEntityMapInline`), `PlayerAdmin` (search trigram on
  full_name/known_as/unique_name; list_filter gender/birth_country/is_active;
  `ProviderEntityMapInline`; readonly provenance fields), `CompetitionAdmin`,
  `SeasonAdmin`, `SeriesAdmin`, `SquadAdmin`/`SquadMemberInline`,
  `MatchStageAdmin`, `FormatCatalogueAdmin`, `DataProviderAdmin` (readonly
  credential_ref note), `ProviderEntityMapAdmin` (search by provider_entity_id,
  filters entity_type/verified), `AuditLogAdmin` (readonly, list_filter
  action/entity_type, search entity_id).
- Readonly provenance: `source_provider_id`, `provider_updated_at`,
  `ingested_at`, `revision` where the model has them.
- Inline `ProviderEntityMap` on Player/Team/Venue with `internal_id` shown.

**Task 5.2 — duplicate-review queue**
- `PlayerDuplicateCandidateAdmin`: list_display (candidate, canonical, score,
  status, created_at), list_filter status, search by player names.
- Actions (with intermediate confirmation pages that show the **feature
  breakdown side by side**):
  - `confirm_merge`: for each candidate, `merge_players(canonical, candidate,
    actor=request.user.uuid-or-label, reason="steward review")`; set
    candidate.status="merged"; guarded by `merge_players` permission.
  - `reject_candidate`: status → "resolved", `reviewed_by`, `reviewed_at`;
    publishes `player_duplicate.reviewed` (audit).
  - `verify_mapping`: sets `verified=True` on the candidate's map row(s) →
    publishes `provider_map.verified` (audit).
- Tests (admin test client): action applies merge; reject marks resolved;
  verify marks map verified; breakdown rendered in the confirmation page.

**Task 5.3 — Roles (Groups data migration) + permission matrix**
- Data migration in `administration`: create Groups `viewer`, `operator`,
  `data_steward`, `admin` and assign permissions:
  - `viewer`: `view_*` on all reference models only (no add/change/delete).
  - `operator`: view + change on reference models (no delete, no merge).
  - `data_steward`: view/add/change/delete on reference models +
    `players.merge_players` + `view_auditlog`.
  - `admin`: every permission incl. providers + audit.
  Reversible (delete the groups).
- `tests/admin/test_permissions.py`: for each role, assert the exact
  `has_perm` matrix (all four roles × a representative model/action); **a
  viewer cannot merge** (admin POST of the confirm-merge action → denied,
  no `PlayerMerge` row); data_steward can merge; operator cannot delete.
- MFA: required by SECURITY_AND_RELIABILITY §1, out of Phase B scope → add a
  **Phase G roadmap ticket** (roadmap Phase G task 7 already covers "Security
  review (RBAC…)"; add explicit "enforce MFA for admin" line) and say so in the
  report (item e).

**Group 5 verification:** `make check`; admin + permission tests green; manual
smoke `make run` + browse `/admin/`.

---

### GROUP 6 — Cricsheet seed importer

**Files:** `apps/players/management/commands/import_cricsheet_register.py`,
`apps/players/import_register.py` (pure core), `apps/players/sinks.py`
(Django sink), `apps/players/settings.py`? (allowlist lives in
`config/settings/base.py`: `CRICSHEET_KEY_ALLOWLIST = ["key_opta", "key_pulse"]`),
curated venue seed `apps/venues/seed/curated_venues.csv`,
`tests/fixtures/people.csv` (≤200 rows), `tests/fixtures/names.csv`,
`tests/fixtures/venues.csv`, `tests/players/test_importer.py`,
`scripts/benchmark_importer.py` (30k-row generator + timing/memory report).

**Task 6.1 — pure core `import_register(rows, sink)`**
- `apps/players/import_register.py`: `import_register(rows: Iterable[dict],
  sink, *, kind: Literal["people","names","venues"]) -> ImportReport` where
  `ImportReport` is a dataclass with `read, created, updated, skipped, errors`
  counts and `errors: list[dict]` (row number + reason). **No Django/DB
  imports.** Rows are `csv.DictReader` records; headers are inspected by the
  caller, not assumed here. `kind` decides which `sink` method is called and
  which keys are meaningful. Bad rows (missing `identifier`/`name`) are counted
  as errors, never raised.
- `apps/players/sinks.py`: `RegisterSink` Protocol + `DjangoRegisterSink`
  implementing:
  - `player(identifier, name, unique_name, keys)`: upsert Player stub + map row
    keyed by (cricsheet provider, "player", identifier) — idempotent;
  - `player_name_variant(identifier, name)`: validate + count; set
    `Player.known_as` from the first variant only when blank (D7);
  - `venue(identifier, name, unique_name, keys)`: upsert Venue + map row; venue
    register carries only `key_cricinfo` (ESPNcricinfo — UX-study-only) so
    **no venue keys are imported by default**;
  - batching: bulk-create map rows in chunks (e.g. 500) where safe.
- Unit tests for `import_register` with a fake sink (pure, no DB): counts,
  bad-row handling, unknown `kind` rejected.

**Task 6.2 — management command**
- `import_cricsheet_register --people PATH [--names PATH] [--venues PATH]
  [--dry-run]`:
  - local files only; `csv.DictReader` on real headers (accept any header order;
    require `identifier` + `name`; ignore unknown columns);
  - **key allowlist**: from each row import the Cricsheet `identifier` plus
    `key_*` columns in `settings.CRICSHEET_KEY_ALLOWLIST` (default
    `["key_opta","key_pulse"]`); **never** `key_cricbuzz`/`key_cricinfo` unless
    the allowlist enables them (note: Phase 0 licensing review decides);
  - venues: `--venues` file, else the **curated CSV** (`apps/venues/seed/
    curated_venues.csv`: `name,city,country_iso,timezone`) committed as seed
    data (clearly commented) with a tiny set of well-known grounds;
  - `--dry-run`: read + validate only, no writes;
  - prints a summary (read/created/updated/skipped/errors) and writes bad rows
    to `<input>.errors.csv` (never crashes);
  - **idempotent**: running twice changes nothing (map rows unique by
    (provider, entity_type, identifier)) — verified by a byte-identical DB
    state test (dump relevant tables before/after or count rows + digests).
  - Docstring states: real `ImportJob`/DLQ tables belong to Phase C
    `ingestion`, which will wrap this command; the core is the pure
    `import_register(rows, sink)` it can call.
- Golden fixtures (committed, ≤200 rows each, hand-written or trimmed real
  extract): `tests/fixtures/people.csv`, `names.csv`, `venues.csv` with realistic
  headers (`identifier,name,unique_name,key_bcci,...,key_pulse`).

**Task 6.3 — importer tests + benchmark**
- Integration: run on the golden people fixture → Player stubs + map rows with
  correct counts; **run twice → identical state** (digest of
  `player` + `provider_entity_map` rows); `--dry-run` writes nothing; bad row →
  errors file, no crash; allowlist respected (a `key_cricbuzz` value is **not**
  imported into any map row by default; enabling it in settings imports it);
  venue curated CSV seeds venues + grounds; `--names` variant sets `known_as`.
- Benchmark (`scripts/benchmark_importer.py`, report-only, not in `make check`):
  generate a synthetic 30k-row people register in `/tmp/opencode`, run the pure
  core + Django sink against a scratch DB, report wall time and peak RSS
  (`tracemalloc`/`resource.getrusage`). Numbers go in the final report (item c).

**Group 6 verification:** `make check`; importer tests green; benchmark script
runs and prints time + peak memory.

---

### GROUP 7 — Public read API

**Files:** `apps/{venues,teams,players,competitions}/api/` packages
(`serializers.py`, `views.py`, `urls.py`, `filters.py`), `config/urls.py`,
`core/http.py` already done, `core/pagination.py` (page-number already exists),
`tests/api/` (contract + golden files + cache + query-count + trigram + 301 +
cursor stability), `docs/CRICKET_API_SPECIFICATION.md` (update), golden
fixtures `tests/api/golden/{team,player,series,players_page}.json`.

**Task 7.1 — serializers + filters**
- Serializers (read-only): `TeamSerializer` (nested country/gb/venue minimal),
  `PlayerSerializer` (birth_country, international_team minimal, role), 
  `CompetitionSerializer` (+ nested seasons on detail), `SeriesSerializer`
  (season/competition nested), `VenueSerializer`. All timestamps UTC ISO-8601.
- `?fields=` sparse support: a `SparseFieldsMixin` on serializers — parse
  `fields` (comma list), validate against `allowed_fields`, restrict
  `get_fields()`; invalid field → `InvalidQuery` (400). 
- Filters: `teams` (q → trigram, category → team_type, country → UUID or ISO
  code), `players` (q, team, country, role), `series` (year → start_date,
  category, type → series_type), `competitions` (q, type).
- Sort allow-lists per endpoint (OrderingFilter with explicit
  `ordering_fields`); anything else → 400.

**Task 7.2 — views + URLs**
- `GET /api/v1/teams`, `/teams/{id}`, `/players`, `/players/{id}`,
  `/competitions`, `/competitions/{id}`, `/series`, `/series/{id}` (plus
  `/venues` and `/venues/{id}` as a bonus — cheap and consistent; document).
- Pagination: teams/competitions/series/venues → `PageNumberPagination`;
  `/players` → **cursor on (name, id) when no `q`** (core CursorPagination with
  `ordering=("name",)`), **page-number ranked by trigram similarity when `q`**.
- Detail views: `get_object` → 404 envelope; **merged player**: look up
  `PlayerMerge` → return `301` with `Location: /api/v1/players/{canonical_id}/`
  (no body, no cache headers on the 301 per spec; documented in API spec §3.5).
- Inactive/deleted rows hidden via selectors (default).
- Each view sets per-view cache TTLs via `CacheControlMixin`:
  teams list/detail 600, players list 300, player detail 300 + swr 600,
  competitions 600, series list 600, series detail 60.
- `config/urls.py` includes each app's `api/urls.py`.
- Tests: envelope shape for item + list; 404 envelope; invalid `?fields=` → 400
  envelope; invalid sort → 400; UUID-only paths (a non-UUID path → 404, not 500).

**Task 7.3 — cache/ETag/304 + 301**
- Contract tests: correct `Cache-Control` per view TTL; strong `ETag` present;
  `If-None-Match` with the etag → `304` (empty body, cache headers preserved);
  changed data → new etag (304 not returned); merged player → `301` + correct
  `Location`; golden-file payloads for one team, one player, one series, and a
  players list page (compare rendered JSON; deterministic via
  `seeded_uuid7_clock` + fixed seed data).

**Task 7.4 — query-count + trigram-index + cursor stability**
- `assertNumQueries`: teams list ≤ 2 (1 data + 1 count), team detail ≤ 2,
  players list ≤ 4 (data + count + prefetches), player detail ≤ 3, series
  detail ≤ 3, competition detail ≤ 3. Fix N+1 until green.
- Trigram EXPLAIN test: `EXPLAIN` the `q` players/teams query with
  `enable_seqscan=off`; assert the plan uses `ix_player_name_trgm` /
  `ix_team_name_trgm` (Index Scan or Bitmap Index Scan).
- Cursor stability: insert rows between two pages; assert no row is skipped or
  duplicated (keyset semantics of the existing core CursorPagination).

**Task 7.5 — schemathesis contract tests + API spec update**
- `tests/api/test_schema_contract.py`: load the OpenAPI schema from the
  `/api/v1/schema` endpoint; run schemathesis against `live_server` for the
  new endpoints with a small `max_examples` (assert 2xx/4xx only, envelope
  shape); if schemathesis is unusable on Python 3.14, validate responses
  against the schema with `jsonschema` instead (record in report, D8).
- Update `CRICKET_API_SPECIFICATION.md`: 301 semantics for merged players;
  `/players` dual pagination (cursor no-q / page-number trigram with q); the
  `?fields=` convention; per-view TTLs; `?provider_id=` admin-only filter
  deferred to the auth phase (note in doc).

**Group 7 verification:** `make check`; all API tests green; `make run` smoke
of each endpoint; schema-ddl/migration-drift unaffected.

---

### GROUP 8 — Docs, gates, roadmap, final sweep

**Task 8.1 — docs**
- `docs/IDENTITY_RESOLUTION.md` (NEW): scoring weights table, normalization
  rules, thresholds (`IDENTITY_MATCH_THRESHOLD=0.95`,
  `DUPLICATE_CANDIDATE_THRESHOLD=0.60`), DoB-mismatch cap, steward workflow
  (queue → review side-by-side → merge/reject/verify), merge semantics
  (redirects, cycles refused, idempotent, **no unmerge**), limits.
- `docs/CRICKET_DATABASE_SCHEMA.md`: add `player_duplicate_candidate` DDL +
  ER node/edge (done in group 2 — verify consistency here).
- `docs/CRICKET_DOMAIN_MODEL.md`: `PlayerDuplicateCandidate` entity entry +
  PlayerMerge/§6 wording updates.
- `docs/CRICKET_BACKEND_ARCHITECTURE.md`: move `FormatCatalogue` from `matches`
  to `competitions` in the module catalog (D3); note the generic
  `resolve()` lives in `providers.services` with per-app entity resolvers (D4).
- `docs/CRICKET_API_SPECIFICATION.md`: group 7 updates; document the schema
  endpoint; note `credential_ref` never carries secrets.
- `docs/CRICKET_IMPLEMENTATION_ROADMAP.md`: mark **Phase B DONE (commit …,
  branch phase-b)** mirroring the A+ block; add the **"Phase B decisions"** list
  (D1–D8) at the end of the Phase B section; add the MFA Phase G ticket; note
  any effort deviation.
- `docs/CRICKET_TESTING_STRATEGY.md` / `README.md`: touch only if a claim
  changes (e.g. drift scope now covers 19 tables).

**Task 8.2 — gates**
- `pyproject.toml`: extend `[tool.coverage.run] source` to the six new apps;
  add `[tool.coverage.report]` override so `make test` gates the new apps at
  ≥ 90%; add a **strict branch gate** for `identity_scoring.py` + `merge.py`
  (100%) as a dedicated pytest invocation, and a `make test-identity` target.
- `Makefile`: `test` target uses the extended source; add `test-identity`;
  `check` runs it.
- `.github/workflows/ci.yml`: mirror the Makefile gates (extended `--cov`
  args; run the identity gate).
- Re-verify `make check` + both drift jobs locally.

**Task 8.3 — final sweep**
- `python manage.py makemigrations --check --dry-run` clean.
- Migration forward+backward test across a DB populated by the importer fixture.
- `git log` review: 8 groups, reviewable commits, no secrets, no generated
  junk; run `grep -r` for `key_cricbuzz`/`key_cricinfo` to confirm no provider
  id is ever imported by default.

**Group 8 verification:** full `make check`; drift jobs; final report.

---

## 5. Decisions (conflicts resolved) — record every one in the final report (d)

- **D1 — Timestamps on reference tables.** Reference DDL §3.2–3.4 lists NO
  `created_at`/`updated_at` on reference entities (only
  `provider_entity_map.created_at`, `player_merge.merged_at`,
  `audit_log.created_at`). DATABASE_SCHEMA wins on structure ⇒ models carry
  exactly the DDL columns; `TimeStampedModel`/`ProvenanceModel` are applied
  only where the DDL declares the fields. DB_CONVENTIONS §6's "every table has
  created_at/updated_at" is superseded by the specific DDL. Consequence: no
  allowlist bloat in the drift check.
- **D2 — `source_provider_id` type.** DDL says `uuid`; task says plain UUID
  column not an FK ⇒ `UUIDField(null=True)` on every model that has it; NOT
  `ProvenanceModel`'s TextField.
- **D3 — `FormatCatalogue` ownership.** ARCHITECTURE assigns it to `matches`
  (Phase C); the task lists it under competitions and Phase B scope. Move it to
  **`competitions`** and update ARCHITECTURE.md in the same change (permitted:
  "If you move any model from the ownership in the doc, update ARCHITECTURE.md
  in the same change"). Its seed matches the DDL exactly; scoring presets agree
  (test).
- **D4 — Where `resolve()` lives.** Task names `players.services.
  identity_resolution`, but the generic resolver must handle teams/venues/
  series/competitions and the import-linter rule forbids `players` importing
  `teams.models`. ⇒ generic `resolve()` in **`providers/services.py`** with a
  per-entity-type resolver registry (`register_entity_resolver`); the player
  resolver + scoring live in **`players/services/identity_resolution.py`**
  (`players/identity_scoring.py` stays pure). Registrations happen in each
  app's `ready()`.
- **D5 — `merged_by`/`actor_id` are plain UUIDs, and Django's auth User has an
  int PK.** Phase B has no user model with a UUID PK ⇒ the merge service takes
  `actor: UUID | str | None`; if a UUID it is stored in `merged_by`, the
  `str()` always goes to `AuditLog.actor_label`, `actor_id` stays NULL. Real
  actor identity arrives with the auth phase.
- **D6 — DoB-mismatch cap.** Both DoBs present and different ⇒ the
  date-of-birth feature scores 0.0 and the **total is capped at 0.25** (below
  both thresholds), so a mismatched pair can never auto-link or auto-merge.
- **D7 — `--names` variant handling.** No alias table exists in the DDL (search
  aliases are Phase F); the first variant per player sets `known_as` only when
  it is blank; all variants are validated and counted.
- **D8 — schemathesis on Python 3.14.** If the pinned schemathesis release does
  not install/run on Python 3.14, the contract mechanism falls back to
  `jsonschema` response validation against the generated OpenAPI schema, and
  the deviation is recorded in the report.

## 6. Review focus (what a reviewer should check)

- Exact DDL parity: constraint/index names, partial predicates, check
  constraints, `GinIndex` opclasses, the `name_key` GeneratedField expression,
  the open-membership `coalesce(left_at,'infinity')` index.
- Six-layer rule: no selector→service imports; no domain→administration
  imports; no teams↔players model-module imports.
- Idempotency + atomicity: resolve/merge/importer concurrency and rollback
  tests are real (threads, transactions), not mocks.
- Security: no secrets, no provider-id leakage, no write endpoints, viewer
  cannot merge, `credential_ref` reference-only.
- Docs move with code (player_duplicate_candidate DDL/ER in the same change as
  the model; FormatCatalogue + ARCHITECTURE in the same change).

## 7. Verification commands

```bash
make check                                    # lint + typecheck + imports + tests + scoring gate
uv run python manage.py makemigrations --check --dry-run
uv run pytest -o addopts="" tests/players/players_tests_identity_scoring.py --cov=apps/players/identity_scoring.py --cov-branch --cov-fail-under=100   # identity gate
# migration-drift (local): build ref_db from the doc, django_db from migrations, diff manifests
python scripts/extract_ddl.py > /tmp/opencode/reference.sql
psql -d ref_db -v ON_ERROR_STOP=1 -f /tmp/opencode/reference.sql
uv run python manage.py migrate --noinput    # into django_db
# compare ci/schema_manifest.sql filtered to ci/schema_drift_scope.txt
uv run python manage.py import_cricsheet_register --people tests/fixtures/people.csv --dry-run
uv run python scripts/benchmark_importer.py  # 30k rows → time + peak memory
```

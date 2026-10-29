# Database Conventions — Cricket OS

These are the rules every migration and model in Cricket OS must follow. They
turn the design in [CRICKET_DATABASE_SCHEMA.md](CRICKET_DATABASE_SCHEMA.md) and
[CRICKET_DOMAIN_MODEL.md](CRICKET_DOMAIN_MODEL.md) into enforceable habits.

## 1. Schema, search path and extensions

- All application tables live in the dedicated PostgreSQL schema **`cricket`**.
  Django connects with `search_path = cricket,public`, so unqualified names
  resolve to `cricket` first and `public` second (for extensions and anything
  Django ships).
- The schema and the `pg_trgm` and `btree_gin` extensions are created by the
  initial migration `apps/core/migrations/0001_initial.py`. Every statement is
  `IF NOT EXISTS`, so the migration is safe to re-run; its reverse is a no-op
  (dropping the schema would destroy data).
- `apps/core/apps.py` also ensures the schema exists on every new database
  connection (`connection_created` signal). Django creates its bookkeeping table
  (`django_migrations`) *before* migrations run, so without this the bookkeeping
  would land in `public` instead of `cricket`.

## 2. Naming

- **snake_case** for every identifier: tables, columns, constraints, indexes.
- Model classes are singular `PascalCase`; the table name is the plural
  snake_case (`Match` → `match`, `PlayerStatistic` → `player_statistic`).
- Foreign keys are `<referenced_singular>_id` (`match_id`, `player_id`).
- Provider identifiers are `provider_<entity>_id` (`provider_match_id`) and are
  **never** primary keys — see §4.

## 3. Column types

| Concept | Type | Notes |
|---|---|---|
| Timestamps | `timestamptz` | Always UTC. Never `timestamp` without a zone. |
| Primary key | `uuid` | Holds a UUIDv7 (see §4). |
| Provider ids | `text` | Opaque; never parsed as a number. |
| Counts (runs, wickets, points) | `integer` | Integral values. |
| Rates (NRR, averages) | `numeric(12,2)` | Fractional values. |
| Booleans | `boolean` | `null` never used as a third state. |
| Flexible payloads | `jsonb` | Only where the schema doc says so. |

Django maps these with `models.DateTimeField` (`USE_TZ = True` → `timestamptz`),
`models.UUIDField`, `models.IntegerField`, `models.DecimalField` and
`models.JSONField`.

## 4. Primary keys and provider identifiers

- **Every table's primary key is a UUIDv7**, generated in the application layer
  by `apps.core.uuid7.uuid7()` and stored natively as `uuid`. UUIDv7 is
  time-ordered, so B-tree inserts stay near-sequential (no v4 fragmentation
  penalty) while IDs remain unguessable and globally unique.
- Do **not** use `gen_random_uuid()` (v4) except as an emergency fallback; do not
  use `bigserial`/`serial`.
- A base model gets its key from `apps.core.models.UUIDPrimaryKeyModel`
  (`id = UUIDField(primary_key=True, default=uuid7, editable=False)`).
- **Provider IDs are never primary keys.** A row keeps its stable internal UUID
  and stores the provider's identifier in a dedicated `provider_*_id` column
  (plus the generic `provider_entity_map` table for multi-provider mapping).
  This means switching or adding a provider never breaks referential integrity.
- **Partitioned tables use a composite primary key that includes the partition
  key** — PostgreSQL requires it, e.g. `PRIMARY KEY (id, match_date)`. Every
  unique index on a partitioned table must include the partition key for the
  same reason (see the `delivery` indexes in the schema doc).

## 5. Constraints and indexes

Give every constraint and index an explicit name so migrations are predictable
and drift checks are meaningful:

| Kind | Pattern | Example |
|---|---|---|
| Unique constraint / index | `ux_<table>_<cols>` | `ux_delivery_revision` |
| Non-unique index | `ix_<table>_<cols>` | `ix_match_start_time_utc` |
| Check constraint | `ck_<table>_<rule>` | `ck_delivery_retirement` |
| Foreign key | `fk_<table>_<target>` | `fk_match_team_home` |
| Primary key | `pk_<table>` | `pk_match` |

Rules:

- Prefer `models.UniqueConstraint(fields=[...], name="ux_...")` over
  `unique_together` (which can't be named).
- Prefer `models.Index(fields=[...], name="ix_...")` over `db_index=True` when
  you need composite indexes or a stable name.
- Partial and functional indexes use
  `models.Index(..., condition=Q(...))` / `models.Index(expressions=[...])`.
- On large/partitioned tables, create indexes concurrently (see
  [MIGRATION_WORKFLOW.md](MIGRATION_WORKFLOW.md)).

## 6. Timestamps, soft delete and provenance

- All timestamps are **`timestamptz`, always UTC**. Never store local time in a
  timestamp column; keep the source's local date/time and timezone in
  `source_*` columns (DOMAIN_MODEL §0).
- `created_at` (set once) and `updated_at` (updated on write) come from
  `apps.core.models.TimeStampedModel`.
- Reference entities use **soft delete**: `is_active boolean` plus
  `deleted_at timestamptz null`. Transactional entities (deliveries, events) are
  append-only.
- **Every ingestion-sourced table carries the provenance columns** from
  DOMAIN_MODEL §0, provided by `apps.core.models.ProvenanceModel`:

  | Column | Meaning |
  |---|---|
  | `source_provider_id` | Which provider supplied the row (NULL = internally created, not ingestion-sourced). |
  | `provider_updated_at` | When the provider says the row last changed. |
  | `ingested_at` | When our pipeline wrote it. |
  | `revision` | Starts at `1`; increments on each correction. |

  Use `apps.core.models.BaseModel` to get the UUIDv7 key, the timestamps and the
  provenance columns in one class.

## 7. Enumerations

- Stable, code-controlled sets are PostgreSQL `ENUM` types in schema `cricket`,
  mirrored by `django.db.models.TextChoices` in `apps/core/enums.py`.
- Enum labels are **exact and ordered**: a member's value must equal the SQL
  label. `tests/test_enums.py` parses §3.1 of the schema doc and fails on any
  drift, so add labels to both places together.
- Extensible sets use a reference table instead (e.g. `dismissal_type` is both
  the stable enum and a reference view).

## 8. Checklist for a new table

- [ ] Table name is plural snake_case; columns are snake_case.
- [ ] Primary key is a UUIDv7 (`UUIDPrimaryKeyModel` / `BaseModel`), composite if
      partitioned.
- [ ] Every constraint and index has an explicit name per §5.
- [ ] Timestamps are `timestamptz`; no naive datetimes.
- [ ] Provider data uses `provider_*_id` columns, never the PK.
- [ ] Ingestion-sourced tables include the provenance columns.
- [ ] Enum values match `apps/core/enums.py` and DATABASE_SCHEMA §3.1.
- [ ] The migration follows [MIGRATION_WORKFLOW.md](MIGRATION_WORKFLOW.md).

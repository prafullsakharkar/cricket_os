"""Create the RANGE-partitioned ``cricket.delivery`` table by hand.

Django cannot create partitioned tables natively, so the DDL is written here
exactly as CRICKET_DATABASE_SCHEMA §3.6 specifies (``PARTITION BY RANGE
(match_date)``, composite PK ``(id, match_date)``, partial unique
``ux_delivery_current``, revision/provider uniques, generated columns, wicket
and retirement check constraints).

Two deliberate deviations from the §3.6 DDL text, both recorded in the Phase C
decisions section of CRICKET_IMPLEMENTATION_ROADMAP.md:

1. **No ``delivery_default`` DEFAULT partition.** The schema-ddl manifest
   treats enum columns as ``text`` and excludes partition children, so this is
   drift-neutral, but it is also *required by the Phase C brief*: yearly
   partitions are created on demand by
   ``deliveries.services.partitions.ensure_partition(year)`` and never catch
   stray rows in a catch-all partition. Pre-creation and archival are Phase G
   (see roadmap).
2. **``dismissal_type`` / ``retirement_type`` are ``text``, not enum types.**
   The rest of the platform (Phase A/B) models enum-like columns as ``text``
   with ``TextChoices`` and the manifest reports enum-typed columns as ``text``
   anyway, so the *effective* schema is identical; using ``text`` avoids
   creating enum types that no other migration uses.

There are no FKs pointing INTO ``delivery`` (PostgreSQL does not support FKs
to partitioned tables); references are logical and service-enforced.

Reverse drops the table. This is safe because no other table references it.
"""

from __future__ import annotations

from django.db import migrations

DDL = """
CREATE TABLE cricket.delivery (
  id              uuid NOT NULL,
  match_date      date NOT NULL,
  match_id        uuid NOT NULL REFERENCES cricket.match(id),
  innings_id      uuid NOT NULL REFERENCES cricket.innings(id),
  over_number     integer NOT NULL,
  ball_in_over    integer NOT NULL,
  delivery_seq    bigint  NOT NULL,
  bowler_id       uuid NOT NULL REFERENCES cricket.player(id),
  batter_id       uuid NOT NULL REFERENCES cricket.player(id),
  non_striker_id  uuid NOT NULL REFERENCES cricket.player(id),
  runs_off_bat    integer NOT NULL DEFAULT 0,
  wides           integer NOT NULL DEFAULT 0,
  noballs         integer NOT NULL DEFAULT 0,
  byes            integer NOT NULL DEFAULT 0,
  leg_byes        integer NOT NULL DEFAULT 0,
  penalty_runs    integer NOT NULL DEFAULT 0,
  is_wicket       boolean NOT NULL DEFAULT false,
  dismissal_type  text,
  dismissed_player_id uuid REFERENCES cricket.player(id),
  bowler_credited_wicket boolean NOT NULL DEFAULT false,
  fielder1_id     uuid REFERENCES cricket.player(id),
  fielder2_id     uuid REFERENCES cricket.player(id),
  fielder3_id     uuid REFERENCES cricket.player(id),
  shot_type       text,
  commentary_text text,
  provider_delivery_id text,
  source_provider_id uuid,
  revision        integer NOT NULL DEFAULT 1,
  is_current      boolean NOT NULL DEFAULT true,
  superseded_at   timestamptz,
  retired_player_id uuid REFERENCES cricket.player(id),
  retirement_type text,
  total_runs      integer GENERATED ALWAYS AS
                  (runs_off_bat + wides + noballs + byes + leg_byes + penalty_runs) STORED,
  is_wide         boolean GENERATED ALWAYS AS (wides > 0) STORED,
  is_noball       boolean GENERATED ALWAYS AS (noballs > 0) STORED,
  is_legal        boolean GENERATED ALWAYS AS (wides = 0 AND noballs = 0) STORED,
  PRIMARY KEY (id, match_date),
  CONSTRAINT ck_delivery_wicket CHECK (
     (is_wicket AND dismissal_type IS NOT NULL AND dismissed_player_id IS NOT NULL)
     OR (NOT is_wicket AND dismissal_type IS NULL)
  ),
  CONSTRAINT ck_delivery_retirement CHECK (
     (retired_player_id IS NULL) = (retirement_type IS NULL)
  ),
  CONSTRAINT ck_delivery_runs_off_bat_nonneg CHECK (runs_off_bat >= 0),
  CONSTRAINT ck_delivery_wides_nonneg CHECK (wides >= 0),
  CONSTRAINT ck_delivery_noballs_nonneg CHECK (noballs >= 0),
  CONSTRAINT ck_delivery_byes_nonneg CHECK (byes >= 0),
  CONSTRAINT ck_delivery_leg_byes_nonneg CHECK (leg_byes >= 0),
  CONSTRAINT ck_delivery_penalty_runs_nonneg CHECK (penalty_runs >= 0)
) PARTITION BY RANGE (match_date);
"""

INDEXES = """
CREATE UNIQUE INDEX ux_delivery_current
  ON cricket.delivery (match_date, innings_id, delivery_seq) WHERE is_current;
CREATE UNIQUE INDEX ux_delivery_revision
  ON cricket.delivery (match_date, innings_id, delivery_seq, revision);
CREATE UNIQUE INDEX ux_delivery_provider
  ON cricket.delivery (match_date, source_provider_id, provider_delivery_id, revision)
  WHERE provider_delivery_id IS NOT NULL;
CREATE INDEX ix_delivery_match ON cricket.delivery (match_date, match_id);
CREATE INDEX ix_delivery_batter ON cricket.delivery (batter_id);
CREATE INDEX ix_delivery_bowler ON cricket.delivery (bowler_id);
"""

REVERSE = "DROP TABLE IF EXISTS cricket.delivery CASCADE;"


class Migration(migrations.Migration):
    dependencies = [
        ("deliveries", "0002_initial"),
        ("matches", "0001_initial"),
        ("innings", "0002_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=DDL, reverse_sql=REVERSE),
        migrations.RunSQL(sql=INDEXES, reverse_sql=migrations.RunSQL.noop),
    ]

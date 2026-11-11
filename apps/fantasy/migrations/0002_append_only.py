"""Append-only + frozen-immutability enforcement (plan decision 1).

Phase G (generic append-only triggers, DB roles, MFA) is NOT in the repo, so
Phase H ships the fantasy-specific enforcement itself:

- ``fantasy_points_ledger``, ``fantasy_point_adjustment`` and
  ``fantasy_calc_audit`` are **append-only**: BEFORE UPDATE/DELETE triggers
  raise for every role (including the table owner / superuser the app connects
  as — REVOKE alone would be bypassed), and UPDATE/DELETE are revoked from
  PUBLIC at the SQL level.
- A frozen ``fantasy_ruleset_version`` and its ``fantasy_point_rule`` rows are
  **immutable**: UPDATE/DELETE of the version, and INSERT/UPDATE/DELETE of its
  rules, raise once the version's ``is_frozen`` is set. Changing rules means a
  new version (never an edit).

Reversible: drops the triggers and functions; the REVOKEs are restored by
regranting to PUBLIC.
"""

from __future__ import annotations

from django.db import migrations

APPEND_ONLY_TRIGGERS = """
CREATE FUNCTION cricket.block_ledger_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fantasy_points_ledger is append-only: updates and deletes are forbidden';
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION cricket.block_adjustment_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fantasy_point_adjustment is append-only: updates and deletes are forbidden';
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION cricket.block_calc_audit_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fantasy_calc_audit is append-only: updates and deletes are forbidden';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_append_only
  BEFORE UPDATE OR DELETE ON cricket.fantasy_points_ledger
  FOR EACH ROW EXECUTE FUNCTION cricket.block_ledger_mutation();

CREATE TRIGGER trg_adjustment_append_only
  BEFORE UPDATE OR DELETE ON cricket.fantasy_point_adjustment
  FOR EACH ROW EXECUTE FUNCTION cricket.block_adjustment_mutation();

CREATE TRIGGER trg_calc_audit_append_only
  BEFORE UPDATE OR DELETE ON cricket.fantasy_calc_audit
  FOR EACH ROW EXECUTE FUNCTION cricket.block_calc_audit_mutation();

REVOKE UPDATE, DELETE ON cricket.fantasy_points_ledger FROM PUBLIC;
REVOKE UPDATE, DELETE ON cricket.fantasy_point_adjustment FROM PUBLIC;
REVOKE UPDATE, DELETE ON cricket.fantasy_calc_audit FROM PUBLIC;
"""

APPEND_ONLY_REVERSE = """
DROP TRIGGER IF EXISTS trg_ledger_append_only ON cricket.fantasy_points_ledger;
DROP TRIGGER IF EXISTS trg_adjustment_append_only ON cricket.fantasy_point_adjustment;
DROP TRIGGER IF EXISTS trg_calc_audit_append_only ON cricket.fantasy_calc_audit;
DROP FUNCTION IF EXISTS cricket.block_ledger_mutation();
DROP FUNCTION IF EXISTS cricket.block_adjustment_mutation();
DROP FUNCTION IF EXISTS cricket.block_calc_audit_mutation();
GRANT UPDATE, DELETE ON cricket.fantasy_points_ledger TO PUBLIC;
GRANT UPDATE, DELETE ON cricket.fantasy_point_adjustment TO PUBLIC;
GRANT UPDATE, DELETE ON cricket.fantasy_calc_audit TO PUBLIC;
"""

FROZEN_IMMUTABILITY = """
CREATE FUNCTION cricket.block_frozen_version_mutation() RETURNS trigger AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.is_frozen THEN
    RAISE EXCEPTION 'fantasy_ruleset_version % is frozen and immutable', OLD.id;
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION cricket.block_frozen_rule_mutation() RETURNS trigger AS $$
DECLARE
  v_frozen boolean;
BEGIN
  SELECT is_frozen INTO v_frozen
  FROM cricket.fantasy_ruleset_version
  WHERE id = COALESCE(NEW.ruleset_version_id, OLD.ruleset_version_id);
  IF v_frozen THEN
    RAISE EXCEPTION 'point rules of frozen ruleset version % are immutable',
      COALESCE(NEW.ruleset_version_id, OLD.ruleset_version_id);
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_version_frozen_immutable
  BEFORE UPDATE OR DELETE ON cricket.fantasy_ruleset_version
  FOR EACH ROW EXECUTE FUNCTION cricket.block_frozen_version_mutation();

CREATE TRIGGER trg_point_rule_frozen_immutable
  BEFORE INSERT OR UPDATE OR DELETE ON cricket.fantasy_point_rule
  FOR EACH ROW EXECUTE FUNCTION cricket.block_frozen_rule_mutation();
"""

FROZEN_REVERSE = """
DROP TRIGGER IF EXISTS trg_version_frozen_immutable ON cricket.fantasy_ruleset_version;
DROP TRIGGER IF EXISTS trg_point_rule_frozen_immutable ON cricket.fantasy_point_rule;
DROP FUNCTION IF EXISTS cricket.block_frozen_version_mutation();
DROP FUNCTION IF EXISTS cricket.block_frozen_rule_mutation();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("fantasy", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=APPEND_ONLY_TRIGGERS,
            reverse_sql=APPEND_ONLY_REVERSE,
        ),
        migrations.RunSQL(
            sql=FROZEN_IMMUTABILITY,
            reverse_sql=FROZEN_REVERSE,
        ),
    ]

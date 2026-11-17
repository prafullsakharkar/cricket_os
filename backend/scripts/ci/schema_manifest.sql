-- Name-agnostic structural manifest of the `cricket` schema, one line per fact.
-- Used to compare the reference DDL database with the Django-migrated database.
-- Enum-typed columns are reported as `text` (Django models store choices as text).
-- Partition children are excluded; FKs are not compared (Django uses DEFERRABLE).
SELECT 'col|' || c.relname || '|' || a.attname || '|' ||
       CASE WHEN t.typtype = 'e' THEN 'text' ELSE format_type(a.atttypid, a.atttypmod) END || '|' ||
       CASE WHEN a.attnotnull THEN 'NOT NULL' ELSE 'NULL' END || '|' ||
       CASE WHEN a.attgenerated <> '' THEN 'generated' ELSE 'plain' END
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
WHERE n.nspname = 'cricket'
  AND c.relkind IN ('r', 'p')
  AND NOT c.relispartition
  AND a.attnum > 0
  AND NOT a.attisdropped
UNION ALL
SELECT 'idx|' || tc.relname || '|' ||
       regexp_replace(pg_get_indexdef(i.indexrelid), 'INDEX \S+ ON', 'INDEX ON')
FROM pg_index i
JOIN pg_class tc ON tc.oid = i.indrelid
JOIN pg_namespace n ON n.oid = tc.relnamespace
WHERE n.nspname = 'cricket'
  AND tc.relkind IN ('r', 'p')
  AND NOT tc.relispartition;

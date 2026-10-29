-- Structural guards run after the reference DDL has been applied.
\set ON_ERROR_STOP on

DO $$
DECLARE
  bad text;
BEGIN
  -- 1. Required extensions
  IF (SELECT count(*) FROM pg_extension WHERE extname IN ('pg_trgm', 'btree_gin')) <> 2 THEN
    RAISE EXCEPTION 'pg_trgm and/or btree_gin are not installed';
  END IF;

  -- 2. delivery is partitioned, and every unique index includes the partition key
  IF NOT EXISTS (SELECT 1 FROM pg_partitioned_table WHERE partrelid = 'cricket.delivery'::regclass) THEN
    RAISE EXCEPTION 'cricket.delivery is not partitioned';
  END IF;

  SELECT string_agg(i.indexrelid::regclass::text, ', ') INTO bad
  FROM pg_index i
  WHERE i.indrelid = 'cricket.delivery'::regclass
    AND i.indisunique
    AND NOT (
      (SELECT attnum FROM pg_attribute
        WHERE attrelid = i.indrelid AND attname = 'match_date') = ANY (i.indkey::int2[])
    );
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'unique indexes on delivery missing match_date: %', bad;
  END IF;

  -- 3. Exactly-one-current-row-per-ball guard is a partial unique index
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'cricket' AND tablename = 'delivery'
      AND indexname = 'ux_delivery_current'
      AND indexdef LIKE 'CREATE UNIQUE INDEX%' AND indexdef LIKE '%is_current%'
  ) THEN
    RAISE EXCEPTION 'ux_delivery_current (partial unique on is_current) is missing';
  END IF;

  -- 4. Trigram search indexes exist (player + team name search)
  IF (SELECT count(*) FROM pg_indexes
       WHERE schemaname = 'cricket' AND indexdef LIKE '%gin_trgm_ops%') < 2 THEN
    RAISE EXCEPTION 'expected at least 2 gin_trgm_ops indexes (player, team)';
  END IF;

  -- 5. Core tables introduced by the doc fixes exist
  IF (SELECT count(*) FROM information_schema.tables
       WHERE table_schema = 'cricket'
         AND table_name IN ('ground','format_catalogue','squad','squad_member',
                            'match_result','match_award','player_merge',
                            'audit_log','match_event_log')) <> 9 THEN
    RAISE EXCEPTION 'one or more tables added in the doc fixes are missing';
  END IF;
END
$$;

SELECT count(*) AS tables_in_cricket_schema
FROM information_schema.tables WHERE table_schema = 'cricket';

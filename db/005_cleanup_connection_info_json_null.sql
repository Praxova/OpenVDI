-- M2-09-fix: backfill rows where connection_info / os_info were written
-- as JSON literal null (the three-character string 'null') instead of
-- SQL NULL. The original cause: SQLAlchemy's default JSONB binding
-- interprets Python `None` as JSON null. The model now uses
-- JSONB(none_as_null=True) and the clearing site uses sql_null();
-- this migration cleans up rows written before the fix.
--
-- Safe to re-run: the WHERE clauses match only the broken rows. If
-- there are none (fresh install, or migration already applied) the
-- UPDATEs are no-ops.
--
-- Verify before running on a populated DB:
--   SELECT count(*) FROM sessions WHERE connection_info::text = 'null';
-- Then run the migration. After:
--   SELECT count(*) FROM sessions WHERE connection_info IS NULL;
-- The latter count should equal the former.

UPDATE sessions
SET connection_info = NULL
WHERE connection_info::text = 'null';

-- Same fix for os_info, in case any rows were written by experimental
-- code. Likely a no-op in M2 since os_info isn't populated until M4.
UPDATE sessions
SET os_info = NULL
WHERE os_info::text = 'null';

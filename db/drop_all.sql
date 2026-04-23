-- OpenVDI nuclear reset.
-- Drops all tables, indexes, and enum types created by 001_schema.sql.
-- Safe to run on an empty or partially-populated DB.
-- Destroys all data. No recovery.

BEGIN;

DROP TABLE IF EXISTS session_metrics CASCADE;
DROP TABLE IF EXISTS audit_log       CASCADE;
DROP TABLE IF EXISTS entitlements    CASCADE;
DROP TABLE IF EXISTS sessions        CASCADE;
DROP TABLE IF EXISTS desktops        CASCADE;
DROP TABLE IF EXISTS pools           CASCADE;
DROP TABLE IF EXISTS templates       CASCADE;
DROP TABLE IF EXISTS clusters        CASCADE;

DROP TYPE  IF EXISTS session_status;
DROP TYPE  IF EXISTS desktop_status;
DROP TYPE  IF EXISTS pool_status;
DROP TYPE  IF EXISTS pool_type;

COMMIT;

-- OpenVDI migration: add 'deleting' to pool_status enum
--
-- M2-15 introduces the async cascade delete flow for pools
-- (DELETE /api/v1/pools/{id}). The endpoint flips the pool row to
-- status='deleting' while a background shim destroys every desktop in
-- the pool in parallel; the pool row itself is removed only after the
-- cascade succeeds. Partial failures leave the pool in this state for
-- operator inspection.
--
-- ADD VALUE IF NOT EXISTS is idempotent so db-reset.sh can apply this
-- after 001_schema.sql (which already carries the value for fresh
-- installs) without raising on existing databases.

ALTER TYPE pool_status ADD VALUE IF NOT EXISTS 'deleting';

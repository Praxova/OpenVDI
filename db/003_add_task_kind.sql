-- OpenVDI migration: add desktops.pve_task_kind
--
-- Records the kind of the in-flight async provider task (provision,
-- destroy, rebuild, start, shutdown, stop). Required for the broker's
-- startup-resume path in app.services.task_tracker.resume_inflight_tasks
-- — when the broker comes up and finds a Desktop row with a non-null
-- pve_task_upid, it needs to know which completion handler to invoke.
--
-- Idempotent via IF NOT EXISTS so db-reset.sh can apply it after
-- 001_schema.sql (which already carries the column for fresh installs)
-- without raising on existing databases.

BEGIN;

ALTER TABLE desktops
    ADD COLUMN IF NOT EXISTS pve_task_kind VARCHAR(32);

COMMIT;

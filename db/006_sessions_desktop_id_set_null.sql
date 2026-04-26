-- M2-15-fix-2: sessions.desktop_id becomes nullable with ON DELETE SET NULL,
-- so the destroy of a desktop nulls the FK on its session rows rather than
-- blocking the destroy outright. Session-row history (duration, protocol,
-- timestamps, future telemetry) survives the desktop's destruction for
-- M3 user-history views and M4 reporting.
--
-- audit_log is unchanged and unrelated. session_metrics.session_id is
-- unchanged because sessions themselves aren't being deleted by this fix.
--
-- Idempotent via DO-block guards against pg_constraint and
-- information_schema.columns.

BEGIN;

DO $$
BEGIN
    -- Drop NOT NULL on desktop_id if still present.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sessions'
          AND column_name = 'desktop_id'
          AND is_nullable = 'NO'
    ) THEN
        ALTER TABLE sessions ALTER COLUMN desktop_id DROP NOT NULL;
    END IF;

    -- Convert the FK to ON DELETE SET NULL only if it isn't already.
    -- pg_constraint.confdeltype values: 'a' = no action, 'r' = restrict,
    -- 'c' = cascade, 'n' = set null, 'd' = set default.
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'sessions_desktop_id_fkey'
          AND confdeltype <> 'n'
    ) THEN
        ALTER TABLE sessions DROP CONSTRAINT sessions_desktop_id_fkey;
        ALTER TABLE sessions
            ADD CONSTRAINT sessions_desktop_id_fkey
            FOREIGN KEY (desktop_id) REFERENCES desktops(id) ON DELETE SET NULL;
    END IF;
END$$;

COMMIT;

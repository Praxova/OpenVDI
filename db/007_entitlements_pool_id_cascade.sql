-- M2-15-fix-3: entitlements.pool_id was created without an ON DELETE
-- clause (defaults to NO ACTION). The Entitlement ORM model declares
-- ondelete="CASCADE", so the model and DB diverged at M2-03 and stayed
-- latent until M2-19 step 22 exercised the path: deleting an empty
-- pool that has entitlements 500s with a FK violation.
--
-- Audit history is preserved in audit_log (which has no FK to either
-- pools or entitlements), so cascading entitlements doesn't lose
-- queryable history.
--
-- Idempotent via DO-block guard against pg_constraint.confdeltype.

BEGIN;

DO $$
BEGIN
    -- Convert the FK to ON DELETE CASCADE only if it isn't already.
    -- pg_constraint.confdeltype values: 'a' = no action, 'r' = restrict,
    -- 'c' = cascade, 'n' = set null, 'd' = set default.
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'entitlements_pool_id_fkey'
          AND confdeltype <> 'c'
    ) THEN
        ALTER TABLE entitlements DROP CONSTRAINT entitlements_pool_id_fkey;
        ALTER TABLE entitlements
            ADD CONSTRAINT entitlements_pool_id_fkey
            FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE;
    END IF;
END$$;

COMMIT;

-- OpenVDI M2 dev seed.
-- One placeholder cluster row with a fixed UUID so the M2 acceptance
-- script can PUT /clusters/<that-uuid> without parsing a POST response.
-- Credentials are intentionally obvious placeholders — they are
-- overwritten by the PUT call early in the acceptance flow.
--
-- No templates, pools, or entitlements here: M2 creates those via the
-- admin API during the acceptance run.

BEGIN;

-- Placeholder cluster — credentials filled in by the M2 acceptance script
-- via PUT /clusters/00000000-0000-0000-0000-00000000c100
INSERT INTO clusters (id, name, provider_type, api_url, token_id, token_secret, status)
VALUES (
    '00000000-0000-0000-0000-00000000c100',
    'default',
    'proxmox',
    'https://CHANGE_ME.example.com:8006',
    'CHANGE_ME@pve!CHANGE_ME',
    '',
    'pending'
);

COMMIT;

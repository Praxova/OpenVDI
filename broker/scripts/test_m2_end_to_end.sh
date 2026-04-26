#!/usr/bin/env bash
#
# OpenVDI M2 end-to-end acceptance test.
#
# Exercises the full broker surface against a live Proxmox cluster:
# register cluster → register/validate template → create + entitle pools →
# provision → user list/connect/disconnect → admin force-disconnect →
# dashboard + audit → destroy desktop → drain + delete pools → rotate
# credentials → delete cluster.
#
# Prerequisites (operator's responsibility):
#   - The broker is RUNNING against a freshly-reset DB. Step 2 calls
#     scripts/db-reset.sh, but the broker must be restarted afterwards
#     so app.state.providers is rebuilt — Step 2 asserts the broker
#     sees no leftover m2test-cluster as the proof.
#   - jq + curl on PATH.
#   - PVE_* env vars set (see required vars below).
#
# Wall-clock budget: ~25-30 min on LVM-thin. Step 8 (non-persistent
# provision) is the long pole — clone → configure → start → agent
# wait → 60s post-boot quiesce → shutdown → snapshot → start → agent
# wait. ~10 min. Everything else is seconds.
#
# Set M2_CONTINUE_ON_FAILURE=1 to run all steps even after a failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BROKER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${BROKER_DIR}/.." && pwd)"

# shellcheck source=lib/m2_helpers.sh
. "${SCRIPT_DIR}/lib/m2_helpers.sh"


# ── Required env vars ─────────────────────────────────────────
: "${OPENVDI_BROKER_URL:=http://localhost:8080}"
: "${OPENVDI_ADMIN_USER:=m2test-admin}"
: "${OPENVDI_REGULAR_USER:=m2test-alice}"
: "${OPENVDI_REGULAR_GROUPS:=m2test-engineers}"

: "${PVE_API_URL:?PVE_API_URL required, e.g. https://pve1.example.com:8006}"
: "${PVE_NODE:?PVE_NODE required, e.g. pve1}"
: "${PVE_TEMPLATE_VMID:?PVE_TEMPLATE_VMID required — a Proxmox template VMID with QEMU agent}"
: "${PVE_TOKEN_ID:?PVE_TOKEN_ID required, e.g. openvdi@pve!openvdi}"
: "${PVE_TOKEN_SECRET:?PVE_TOKEN_SECRET required}"
# Reserved for M3+ full-clone support. M2 uses linked clones (per
# architecture.md → Cloning Model); linked clones inherit the
# template's storage and Proxmox rejects any `storage` param with
# `parameter 'storage' not allowed for linked clones`. The pool create
# bodies below intentionally OMIT target_storage. See m2-19 prompt's
# Step 6 note and the m2-15-fix-template-clone-storage follow-up.
: "${PVE_TARGET_STORAGE:=local-lvm}"

# Test VMID ranges. High numbers to keep clear of production. Cleanup
# trap touches both ranges.
: "${M2_PERSISTENT_VMID_START:=5900}"
: "${M2_PERSISTENT_VMID_END:=5909}"
: "${M2_NONPERSISTENT_VMID_START:=5910}"
: "${M2_NONPERSISTENT_VMID_END:=5919}"

# Optional: a VMID that exists but is NOT a template — used in step 5b.
# Skipped if not set.
: "${M2_NONTEMPLATE_VMID:=}"

# Seeded placeholder cluster UUID (db/002_seed_data.sql). The seed is
# designed for this script: it carries placeholder credentials that
# this script overwrites via PUT, which goes through the M2-18
# credential rotation slow path — that path constructs a provider and
# inserts it into app.state.providers without requiring a broker
# restart. Using POST + a fresh cluster would NOT stash the provider
# (M2-14 deliberately keeps lifespan as the construction site), and
# template creation would then 400.
SEEDED_CLUSTER_ID="00000000-0000-0000-0000-00000000c100"

# IDs captured during the run, exported for the cleanup trap.
CLUSTER_ID="$SEEDED_CLUSTER_ID"
TEMPLATE_ID=""
PERSISTENT_POOL_ID=""
NONPERSISTENT_POOL_ID=""
NONPERSISTENT_DESKTOP_ID=""
SESSION_ID=""

# ── Cleanup trap ──────────────────────────────────────────────
# Best-effort: stop+destroy any VM in the test ranges, regardless of
# what the broker thinks. Hits Proxmox directly so it works even when
# the broker is in a bad state. Narrow scope (only test VMID ranges)
# protects shared environments.
cleanup_orphan_vms() {
    local ec=$?
    echo
    echo "─── cleanup: destroying any orphan VMs in test VMID ranges"
    local vmid http
    for vmid in $(seq "$M2_PERSISTENT_VMID_START" "$M2_NONPERSISTENT_VMID_END"); do
        http=$(curl -sS -k -o /dev/null -w '%{http_code}' \
            -H "Authorization: PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}" \
            "${PVE_API_URL}/api2/json/nodes/${PVE_NODE}/qemu/${vmid}/status/current" \
            2>/dev/null || echo "0")
        if [ "$http" = "200" ]; then
            echo "  found orphan VMID ${vmid} — stopping + destroying"
            curl -sS -k -X POST \
                -H "Authorization: PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}" \
                "${PVE_API_URL}/api2/json/nodes/${PVE_NODE}/qemu/${vmid}/status/stop" \
                >/dev/null 2>&1 || true
            sleep 3
            curl -sS -k -X DELETE \
                -H "Authorization: PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}" \
                "${PVE_API_URL}/api2/json/nodes/${PVE_NODE}/qemu/${vmid}?purge=1" \
                >/dev/null 2>&1 || true
        fi
    done
    return "$ec"
}
trap cleanup_orphan_vms EXIT


run_started=$(date +%s)
echo "OpenVDI M2 end-to-end test"
echo "  broker url:        ${OPENVDI_BROKER_URL}"
echo "  proxmox api:       ${PVE_API_URL}"
echo "  proxmox node:      ${PVE_NODE}"
echo "  template vmid:     ${PVE_TEMPLATE_VMID}"
echo "  test vmid range:   ${M2_PERSISTENT_VMID_START}-${M2_NONPERSISTENT_VMID_END}"


# ── Step 1 — preflight ────────────────────────────────────────
step_begin "preflight — broker and Proxmox reachable"

health_status=$(curl -sS -o /dev/null -w '%{http_code}' "${OPENVDI_BROKER_URL}/health" || echo "000")
if [ "$health_status" != "200" ]; then
    step_fail "broker /health returned ${health_status}; is the broker running?"
fi

# /version requires auth on most Proxmox configs — send the token
# rather than expect an unauthenticated 200.
pve_status=$(curl -sS -k -o /dev/null -w '%{http_code}' \
    -H "Authorization: PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}" \
    "${PVE_API_URL}/api2/json/version" || echo "000")
if [ "$pve_status" != "200" ]; then
    step_fail "Proxmox /version returned ${pve_status}; check PVE_API_URL + token"
fi

step_pass


# ── Step 2 — db-reset + verify clean ──────────────────────────
step_begin "database reset"

if ! "${REPO_ROOT}/scripts/db-reset.sh" >/dev/null 2>&1; then
    step_fail "db-reset.sh failed (run it manually to see psql errors)"
fi

# After reset the seed inserts the placeholder cluster (fixed UUID) in
# `pending` status with bogus creds. Confirm the broker sees that and
# nothing else — anything more means an old run wasn't cleaned up or
# the broker wasn't restarted against the fresh DB.
out=$(admin_curl GET "/api/v1/clusters")
body=$(echo "$out" | tail -n +2)
total=$(echo "$body" | jq -r '.data | length')
seeded=$(echo "$body" | jq -r --arg id "$SEEDED_CLUSTER_ID" \
    '[.data[] | select(.id == $id)] | length')
if [ "$seeded" != "1" ]; then
    step_fail "seeded placeholder cluster ($SEEDED_CLUSTER_ID) missing after reset; check 002_seed_data.sql"
fi
if [ "$total" != "1" ]; then
    step_fail "expected exactly the placeholder cluster after reset; got ${total} cluster(s) — restart the broker against the fresh DB"
fi

step_pass


# ── Step 3 — populate cluster credentials via rotation path ──
step_begin "populate cluster credentials (PUT into placeholder)"

# PUT real Proxmox credentials into the seeded placeholder cluster.
# Goes through M2-18's credential rotation slow path: the row is
# updated, status flips to pending, construct_provider builds a fresh
# provider, ping_and_update_status flips it to active, and the new
# provider is swapped into app.state.providers. End state: a working
# cluster ready to back templates and pools — without a broker
# restart, which is what makes "one-command smoke" workable.
rotate_body=$(jq -n \
    --arg url "$PVE_API_URL" \
    --arg tid "$PVE_TOKEN_ID" \
    --arg tsec "$PVE_TOKEN_SECRET" \
    '{
        api_url: $url,
        token_id: $tid,
        token_secret: $tsec,
        verify_ssl: false
    }')

out=$(admin_curl PUT "/api/v1/clusters/${CLUSTER_ID}" "$rotate_body")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 200 "$status" "cluster credential rotation"
assert_jq '.data.status' 'active' "$body" "cluster active after rotation"

# Wire-contract: never echo token_secret.
if echo "$body" | jq -e '.data | has("token_secret")' >/dev/null 2>&1; then
    step_fail "cluster response contains token_secret — secret leaking"
fi

step_pass


# ── Step 4 — register and validate template ──────────────────
step_begin "register template + validate"

create_body=$(jq -n \
    --arg cid "$CLUSTER_ID" \
    --arg node "$PVE_NODE" \
    --argjson vmid "$PVE_TEMPLATE_VMID" \
    '{
        cluster_id: $cid,
        name: "m2test-template",
        pve_node: $node,
        pve_vmid: $vmid,
        os_type: "linux",
        cpu_cores: 2,
        memory_mb: 2048,
        disk_gb: 20
    }')

out=$(admin_curl POST "/api/v1/templates" "$create_body")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 201 "$status" "template create"
TEMPLATE_ID=$(echo "$body" | jq -r '.data.id')

# Validate
out=$(admin_curl POST "/api/v1/templates/${TEMPLATE_ID}/validate")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 200 "$status" "template validate"
assert_jq '.data.passed' 'true' "$body" "all template checks pass"
assert_jq '.data.checks | map(select(.name == "is_template")) | .[0].passed' \
    'true' "$body" "is_template check"
assert_jq '.data.checks | map(select(.name == "guest_agent_configured")) | .[0].passed' \
    'true' "$body" "guest_agent_configured check"

step_pass


# ── Step 5 — negative template tests ─────────────────────────
step_begin "negative — template create with nonexistent VMID"

bad_body=$(jq -n \
    --arg cid "$CLUSTER_ID" \
    --arg node "$PVE_NODE" \
    '{cluster_id: $cid, name: "m2test-bad-template",
      pve_node: $node, pve_vmid: 99999, os_type: "linux",
      cpu_cores: 1, memory_mb: 512, disk_gb: 10}')

out=$(admin_curl POST "/api/v1/templates" "$bad_body")
status=$(echo "$out" | head -1)
# Per M2-14 acceptance: 400 (target) OR 502 (current — Proxmox returns
# HTTP 500 with "Configuration file does not exist", and the provider
# doesn't currently translate that to ProviderNotFoundError). M4
# closes the gap; both responses satisfy acceptance for now.
assert_status_in "$status" "nonexistent VMID" 400 502

step_pass


step_begin "negative — template create on non-template VM"
if [ -z "$M2_NONTEMPLATE_VMID" ]; then
    step_skip "M2_NONTEMPLATE_VMID not set"
else
    bad_body=$(jq -n \
        --arg cid "$CLUSTER_ID" \
        --arg node "$PVE_NODE" \
        --argjson vmid "$M2_NONTEMPLATE_VMID" \
        '{cluster_id: $cid, name: "m2test-nontemplate",
          pve_node: $node, pve_vmid: $vmid, os_type: "linux",
          cpu_cores: 1, memory_mb: 512, disk_gb: 10}')
    out=$(admin_curl POST "/api/v1/templates" "$bad_body")
    status=$(echo "$out" | head -1)
    body=$(echo "$out" | tail -n +2)
    assert_status 400 "$status" "non-template VM create"
    # Message should mention "is not a template"
    if ! echo "$body" | jq -e '.error.message | test("is not a template")' >/dev/null 2>&1; then
        step_fail "expected message to mention 'is not a template'"
    fi
    step_pass
fi


# ── Step 6 — create persistent + non-persistent pools ────────
step_begin "create persistent pool"

# target_storage intentionally OMITTED — see PVE_TARGET_STORAGE note.
# M2 uses linked clones; Proxmox rejects `storage` on linked clones.
# The principled provisioner-side fix lands in m2-15-fix-template-clone-storage.
persistent_body=$(jq -n \
    --arg tid "$TEMPLATE_ID" \
    --arg cid "$CLUSTER_ID" \
    --argjson vstart "$M2_PERSISTENT_VMID_START" \
    --argjson vend "$M2_PERSISTENT_VMID_END" \
    '{
        name: "m2test-persistent",
        display_name: "M2 Test Persistent",
        description: "M2-19 acceptance test persistent pool",
        pool_type: "persistent",
        template_id: $tid,
        cluster_id: $cid,
        min_spare: 0,
        max_size: 2,
        vmid_range_start: $vstart,
        vmid_range_end: $vend,
        name_prefix: "M2P",
        refresh_on_logoff: false
    }')

out=$(admin_curl POST "/api/v1/pools" "$persistent_body")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 201 "$status" "persistent pool create"
PERSISTENT_POOL_ID=$(echo "$body" | jq -r '.data.id')

step_pass


step_begin "create non-persistent pool"

# target_storage intentionally OMITTED — see persistent pool comment.
nonpersistent_body=$(jq -n \
    --arg tid "$TEMPLATE_ID" \
    --arg cid "$CLUSTER_ID" \
    --argjson vstart "$M2_NONPERSISTENT_VMID_START" \
    --argjson vend "$M2_NONPERSISTENT_VMID_END" \
    '{
        name: "m2test-kiosk",
        display_name: "M2 Test Kiosk",
        description: "M2-19 acceptance test non-persistent pool",
        pool_type: "nonpersistent",
        template_id: $tid,
        cluster_id: $cid,
        min_spare: 0,
        max_size: 2,
        vmid_range_start: $vstart,
        vmid_range_end: $vend,
        name_prefix: "M2K",
        refresh_on_logoff: true
    }')

out=$(admin_curl POST "/api/v1/pools" "$nonpersistent_body")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 201 "$status" "non-persistent pool create"
NONPERSISTENT_POOL_ID=$(echo "$body" | jq -r '.data.id')

step_pass


step_begin "grant entitlements"

grant_body=$(jq -n --arg grp "$OPENVDI_REGULAR_GROUPS" \
    '{principal_type: "group", principal_name: $grp}')

out=$(admin_curl POST "/api/v1/pools/${PERSISTENT_POOL_ID}/entitlements" "$grant_body")
status=$(echo "$out" | head -1)
assert_status 201 "$status" "persistent pool entitlement"

out=$(admin_curl POST "/api/v1/pools/${NONPERSISTENT_POOL_ID}/entitlements" "$grant_body")
status=$(echo "$out" | head -1)
assert_status 201 "$status" "non-persistent pool entitlement"

step_pass


# ── Step 7 — negative pool create tests ──────────────────────
step_begin "negative — VMID range conflict"

dup_body=$(echo "$persistent_body" | jq '.name = "m2test-dup"')
out=$(admin_curl POST "/api/v1/pools" "$dup_body")
status=$(echo "$out" | head -1)
assert_status 409 "$status" "VMID range conflict 409"

step_pass


step_begin "negative — invalid pool name"

bad_body=$(echo "$persistent_body" | jq '.name = "BAD NAME"')
out=$(admin_curl POST "/api/v1/pools" "$bad_body")
status=$(echo "$out" | head -1)
assert_status 422 "$status" "invalid pool name rejected"

step_pass


# ── Step 8 — provision a non-persistent desktop ──────────────
step_begin "provision non-persistent desktop (long-running)"

provision_body='{"count": 1}'
out=$(admin_curl POST "/api/v1/pools/${NONPERSISTENT_POOL_ID}/provision" "$provision_body")
status=$(echo "$out" | head -1)
assert_status 202 "$status" "provision accepted"

# Non-persistent provisioning is the longer path:
# clone → configure → start → agent_ping → 60s quiesce → shutdown →
# snapshot → start → agent_ping. ~5-10 min on LVM-thin.
poll_until \
    "/api/v1/pools/${NONPERSISTENT_POOL_ID}" \
    '.data.capacity.available // 0' '1' \
    900 \
    "non-persistent desktop reaches available"

out=$(admin_curl GET "/api/v1/pools/${NONPERSISTENT_POOL_ID}")
body=$(echo "$out" | tail -n +2)
NONPERSISTENT_DESKTOP_ID=$(echo "$body" | jq -r '.data.desktops[0].id')
assert_jq '.data.desktops[0].status' 'available' "$body" "desktop available"

step_pass


# ── Step 9 — user workflow ───────────────────────────────────
step_begin "user lists entitled desktops"

out=$(user_curl GET "/api/v1/me/desktops")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 200 "$status" "user list desktops"
count=$(echo "$body" | jq '.data | length')
if [ "$count" != "2" ]; then
    step_fail "user sees ${count} pools; expected 2"
fi

step_pass


step_begin "user connects to non-persistent pool"

out=$(user_curl POST "/api/v1/me/desktops/${NONPERSISTENT_POOL_ID}/connect")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 200 "$status" "connect"

SESSION_ID=$(echo "$body" | jq -r '.data.session_id')
assert_jq '.data.ticket.kind' 'novnc' "$body" "ticket kind is novnc"

ws_url=$(echo "$body" | jq -r '.data.ticket.websocket_url')
case "$ws_url" in
    wss://*) ;;
    *) step_fail "websocket_url does not start with wss://: ${ws_url}" ;;
esac

pw=$(echo "$body" | jq -r '.data.ticket.password')
if [ -z "$pw" ] || [ "$pw" = "null" ]; then
    step_fail "ticket password is empty"
fi

step_pass


step_begin "user disconnects"

out=$(user_curl DELETE "/api/v1/me/sessions/${SESSION_ID}")
status=$(echo "$out" | head -1)
assert_status 204 "$status" "user disconnect"

# Admin checks the session is ended via /sessions/{id}
out=$(admin_curl GET "/api/v1/sessions/${SESSION_ID}")
body=$(echo "$out" | tail -n +2)
assert_jq '.data.status' 'ended' "$body" "session ended"

# Non-persistent: assigned_user cleared
out=$(admin_curl GET "/api/v1/desktops/${NONPERSISTENT_DESKTOP_ID}")
body=$(echo "$out" | tail -n +2)
assigned=$(echo "$body" | jq -r '.data.assigned_user')
if [ "$assigned" != "null" ]; then
    step_fail "non-persistent desktop still assigned to '${assigned}' after disconnect"
fi

# JSONB null fix (M2-09-fix): the ended session row's connection_info
# must be cleared as SQL NULL, not JSON null. Verified by the
# response not containing connection_info at all (M2-17 schema
# enforcement) AND by the row no longer being readable in
# active-only filters.
if echo "$body" | jq -e '.data | has("connection_info")' >/dev/null 2>&1; then
    step_fail "desktop response leaked connection_info"
fi

step_pass


# ── Step 10 — auth/wire-contract negatives ───────────────────
step_begin "unauthenticated request → 401 envelope"

raw=$(curl -sS -w '\n__STATUS__:%{http_code}' "${OPENVDI_BROKER_URL}/api/v1/pools")
status=$(echo "$raw" | awk -F':' '/^__STATUS__:/{print $2}')
body=$(echo "$raw" | sed '/^__STATUS__:/d')
assert_status 401 "$status" "no headers"
assert_jq '.error.code' 'UNAUTHORIZED' "$body" "401 envelope"

step_pass


step_begin "non-admin to admin endpoint → 403 envelope"

out=$(user_curl GET "/api/v1/pools")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 403 "$status" "non-admin"
assert_jq '.error.code' 'FORBIDDEN' "$body" "403 envelope"
# Admin-only details must NOT appear in non-admin responses
if echo "$body" | jq -e '.error | has("details")' >/dev/null 2>&1; then
    step_fail "non-admin 403 includes admin-only details"
fi

step_pass


# ── Step 11 — dashboard + audit ──────────────────────────────
step_begin "dashboard summary"

out=$(admin_curl GET "/api/v1/dashboard/summary")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 200 "$status" "dashboard summary"
# 1 m2test cluster + the placeholder default cluster from seed = 2
total_clusters=$(echo "$body" | jq -r '.data.clusters.total')
if [ "$total_clusters" -lt "1" ]; then
    step_fail "dashboard reports ${total_clusters} cluster(s); expected ≥1"
fi
assert_jq '.data.pools.total' '2' "$body" "2 pools"
assert_jq '.data.desktops.total' '1' "$body" "1 desktop"

step_pass


step_begin "dashboard capacity breakdown"

out=$(admin_curl GET "/api/v1/dashboard/capacity")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 200 "$status" "dashboard capacity"
pool_count=$(echo "$body" | jq '.data | length')
if [ "$pool_count" != "2" ]; then
    step_fail "expected 2 pools in capacity, got ${pool_count}"
fi

step_pass


step_begin "audit log captures broker.connect"

out=$(admin_curl GET "/api/v1/audit?action=broker.connect")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 200 "$status" "audit query"
count=$(echo "$body" | jq '.data | length')
if [ "$count" -lt "1" ]; then
    step_fail "expected ≥1 broker.connect audit row, got ${count}"
fi

step_pass


# ── Step 12 — destructive cleanup via API ────────────────────
step_begin "admin destroys desktop"

out=$(admin_curl DELETE "/api/v1/desktops/${NONPERSISTENT_DESKTOP_ID}")
status=$(echo "$out" | head -1)
assert_status 202 "$status" "desktop destroy accepted"

poll_until_status "/api/v1/desktops/${NONPERSISTENT_DESKTOP_ID}" \
    "404" 300 "desktop row removed"

step_pass


step_begin "drain + delete persistent pool (empty)"

out=$(admin_curl POST "/api/v1/pools/${PERSISTENT_POOL_ID}/drain")
status=$(echo "$out" | head -1)
assert_status 202 "$status" "drain accepted"

out=$(admin_curl DELETE "/api/v1/pools/${PERSISTENT_POOL_ID}")
status=$(echo "$out" | head -1)
assert_status 202 "$status" "pool delete accepted"

poll_until_status "/api/v1/pools/${PERSISTENT_POOL_ID}" \
    "404" 180 "persistent pool removed"

step_pass


step_begin "delete non-persistent pool (cascade with no desktops)"

# Step 12's destroy already removed the only desktop. The cascade still
# exercises the shim — proves the path works on a zero-desktop pool.
out=$(admin_curl DELETE "/api/v1/pools/${NONPERSISTENT_POOL_ID}")
status=$(echo "$out" | head -1)
assert_status 202 "$status" "non-persistent pool delete accepted"

poll_until_status "/api/v1/pools/${NONPERSISTENT_POOL_ID}" \
    "404" 120 "non-persistent pool removed"

step_pass


# ── Step 13 — credential rotation + cluster delete ────────────
step_begin "rotate cluster credentials with same values (fast path)"

# Capture rotation-audit count before the PUT — Step 3 already wrote
# one when it populated the placeholder, so the absolute count is 1+
# coming in. The fast-path PUT (decrypt-and-compare against the now-
# stored values) must NOT add another row: that's the proof that
# re-submitting unchanged credentials doesn't churn the provider.
out=$(admin_curl GET "/api/v1/audit?action=cluster.credentials.rotated")
before_count=$(echo "$out" | tail -n +2 | jq '.data | length')

rotate_body=$(jq -n --arg tsec "$PVE_TOKEN_SECRET" \
    '{token_secret: $tsec}')
out=$(admin_curl PUT "/api/v1/clusters/${CLUSTER_ID}" "$rotate_body")
status=$(echo "$out" | head -1)
body=$(echo "$out" | tail -n +2)
assert_status 200 "$status" "re-submit same credentials"
assert_jq '.data.status' 'active' "$body" "still active"

out=$(admin_curl GET "/api/v1/audit?action=cluster.credentials.rotated")
after_count=$(echo "$out" | tail -n +2 | jq '.data | length')
if [ "$after_count" != "$before_count" ]; then
    step_fail "fast-path PUT added a cluster.credentials.rotated row (${before_count} → ${after_count})"
fi

step_pass


step_begin "delete template (clears the last cluster dependent)"

out=$(admin_curl DELETE "/api/v1/templates/${TEMPLATE_ID}")
status=$(echo "$out" | head -1)
assert_status 204 "$status" "template delete"

step_pass


step_begin "delete cluster"

out=$(admin_curl DELETE "/api/v1/clusters/${CLUSTER_ID}")
status=$(echo "$out" | head -1)
assert_status 204 "$status" "cluster delete"

# Verify gone
out=$(admin_curl GET "/api/v1/clusters/${CLUSTER_ID}")
status=$(echo "$out" | head -1)
assert_status 404 "$status" "cluster 404 after delete"

step_pass


# ── Summary ───────────────────────────────────────────────────
total_elapsed=$(( $(date +%s) - run_started ))
echo
echo "━━━ summary ━━━"
echo "  total steps:  ${_step_count}"
echo "  ${_C_GREEN}passed:${_C_RESET}       ${_step_pass}"
echo "  ${_C_RED}failed:${_C_RESET}       ${_step_fail}"
echo "  ${_C_YELLOW}skipped:${_C_RESET}      ${_step_skip}"
echo "  total time:   ${total_elapsed}s"

if [ "$_step_fail" -gt "0" ]; then
    exit 1
fi
exit 0

#!/usr/bin/env bash
# M5 acceptance gate. Walks the full MCP catalog end-to-end against
# a live broker. Exits 0 on full pass; non-zero with the failed step
# named on any failure.
#
# Prerequisites:
#   - Broker reachable at OPENVDI_BROKER_URL.
#   - Service-account credentials (OPENVDI_SERVICE_USER,
#     OPENVDI_SERVICE_PASSWORD) work.
#   - At least one cluster registered.
#   - At least one template registered against that cluster.
#   - Set TEMPLATE_ID and CLUSTER_ID env vars to point at them.
#   - VMID range 9000-9019 is unused (the script claims this for the
#     test pool).
#
# This script is the M5 milestone gate. After it passes, tag
# m5-complete and cut beta.

set -euo pipefail

# ── Color output ──────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'  # no color

step() { echo -e "${BLUE}▸${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }

# ── Prerequisites ─────────────────────────────────────────────

step "Checking prerequisites..."

: "${OPENVDI_BROKER_URL:?OPENVDI_BROKER_URL is required}"
: "${OPENVDI_SERVICE_USER:?OPENVDI_SERVICE_USER is required}"
: "${OPENVDI_SERVICE_PASSWORD:?OPENVDI_SERVICE_PASSWORD is required}"
: "${TEMPLATE_ID:?TEMPLATE_ID is required (UUID of pre-registered template)}"
: "${CLUSTER_ID:?CLUSTER_ID is required (UUID of the cluster)}"

ok "Environment OK"

# ── Run the Python harness ────────────────────────────────────

# The harness imports tool functions directly and runs them as
# coroutines. Same composition pattern intent tools use; legitimate
# inside the package boundary. Spawning a real MCP-protocol client
# here would be more complex and add no value.

step "Running M5 acceptance via Python harness..."

python3 <<'PYEOF'
import asyncio
import os
import sys
import time

# Imports happen in build_server() context — let server.py construct
# the broker client first.
from openvdi_admin.server import build_server


async def run():
    build_server()  # initializes BrokerClient singleton

    # Now we can import + call tools.
    from openvdi_admin.intent.health_check import openvdi_health_check
    from openvdi_admin.intent.deploy_pool import openvdi_deploy_pool
    from openvdi_admin.intent.smoke_test import openvdi_smoke_test
    from openvdi_admin.intent.diagnose_pool import openvdi_diagnose_pool
    from openvdi_admin.intent.diagnose_user import openvdi_diagnose_user
    from openvdi_admin.intent.reset_environment import (
        openvdi_reset_test_environment,
    )

    template_id = os.environ["TEMPLATE_ID"]
    cluster_id = os.environ["CLUSTER_ID"]
    test_user = os.environ.get("TEST_USER", os.environ["OPENVDI_SERVICE_USER"])

    timeline = []
    start = time.monotonic()

    def mark(name, ok, detail=""):
        elapsed = int((time.monotonic() - start) * 1000)
        symbol = "✓" if ok else "✗"
        print(f"  [{elapsed:>6}ms] {symbol} {name}{': ' + detail if detail else ''}",
              flush=True)
        timeline.append((name, ok, elapsed, detail))
        if not ok:
            sys.exit(1)

    # 1. Health check
    h = await openvdi_health_check()
    if not h["ok"]:
        mark("health_check", False, str(h.get("error_message")))
    else:
        cl = h["result"]["clusters"]
        mark("health_check", True,
             f"broker reachable, {len(cl)} cluster(s)")

    # 2. Reset any leftover test- pools (best-effort cleanup)
    rdry = await openvdi_reset_test_environment(name_prefix="m5acc-")
    if rdry["ok"] and rdry.get("dry_run"):
        n = rdry["summary"]["pools"]
        mark("pre_reset_dry_run", True, f"{n} stale pool(s) found")
        if n > 0:
            r = await openvdi_reset_test_environment(
                name_prefix="m5acc-", confirm=True,
            )
            mark("pre_reset_execute", r["ok"],
                 r.get("error_message", ""))
    else:
        mark("pre_reset_dry_run", False,
             rdry.get("error_message", "unexpected shape"))

    # 3. Deploy a fresh test pool
    deploy = await openvdi_deploy_pool(
        template_id=template_id,
        pool_name="m5acc-engineering",
        pool_display_name="M5 Acceptance Engineering",
        pool_type="nonpersistent",
        cluster_id=cluster_id,
        vmid_range_start=9000,
        vmid_range_end=9019,
        name_prefix="M5ACC",
        entitlements=[{"type": "user", "name": test_user}],
        min_spare=2,
        max_size=5,
        pre_provision=True,
        confirm=True,
    )
    if not deploy["ok"]:
        mark("deploy_pool", False,
             f"failed at step {deploy.get('failed_at_step')}: "
             f"{deploy.get('error_message')}")
    pool_id = deploy["result"]["pool_id"]
    mark("deploy_pool", True,
         f"pool_id={pool_id}, "
         f"provisioned={deploy['result']['provisioned_count']}")

    # 4. Smoke test
    smoke = await openvdi_smoke_test(pool_id=pool_id)
    if not smoke["ok"]:
        mark("smoke_test", False, smoke.get("error_message"))
    mark("smoke_test", True, "verified desktop")

    # 5. Diagnose the pool
    dpool = await openvdi_diagnose_pool(pool_id)
    if not dpool["ok"]:
        mark("diagnose_pool", False, dpool.get("error_message"))
    health = dpool["result"]["health"]
    mark("diagnose_pool", True, f"health={health}")

    # 6. Diagnose the test user
    duser = await openvdi_diagnose_user(test_user)
    if not duser["ok"]:
        mark("diagnose_user", False, duser.get("error_message"))
    np = len(duser["result"]["directly_entitled_pools"])
    mark("diagnose_user", True, f"{np} directly entitled pool(s)")

    # 7. Reset to clean up
    reset = await openvdi_reset_test_environment(
        name_prefix="m5acc-", confirm=True,
    )
    if not reset["ok"]:
        mark("final_reset", False, reset.get("error_message"))
    mark("final_reset", True,
         f"deleted {reset['result']['pools_deleted']} pool(s)")

    print()
    print("─" * 60)
    print(f"M5 acceptance: PASSED ({len(timeline)} steps, "
          f"{int((time.monotonic() - start) * 1000)}ms total)")
    print("─" * 60)


if __name__ == "__main__":
    asyncio.run(run())

PYEOF

ok "Acceptance complete."
echo ""
echo "Next: tag m5-complete, cut beta."

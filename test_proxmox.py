#!/usr/bin/env python3
"""Standalone Milestone 1 test: clone, start, wait for agent, VNC ticket, destroy.

Usage:
    # From repo root with .env configured:
    python test_proxmox.py

    # Or with explicit overrides:
    PVE_API_URL=https://10.0.0.2:8006 \
    PVE_TOKEN_ID=openvdi@pve!openvdi \
    PVE_TOKEN_SECRET=xxx \
    python test_proxmox.py
"""

import asyncio
import logging
import sys

from broker.app.config import settings
from broker.app.proxmox.client import ProxmoxClient
from broker.app.proxmox.exceptions import ProxmoxError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
log = logging.getLogger("test_proxmox")

# ── Test parameters ───────────────────────────────────────
NODE = settings.default_node
TEMPLATE_VMID = settings.default_template_vmid
CLONE_VMID = 9999  # Throwaway VMID for testing
CLONE_NAME = "openvdi-test-clone"


async def main() -> int:
    log.info("=== OpenVDI Milestone 1: Proxmox Integration Test ===")
    log.info("Target: %s  Node: %s  Template: %d", settings.pve_api_url, NODE, TEMPLATE_VMID)

    if not settings.pve_token_id or not settings.pve_token_secret:
        log.error("PVE_TOKEN_ID and PVE_TOKEN_SECRET must be set in .env or environment")
        return 1

    async with ProxmoxClient(
        api_url=settings.pve_api_url,
        token_id=settings.pve_token_id,
        token_secret=settings.pve_token_secret,
        verify_ssl=settings.pve_verify_ssl,
    ) as pve:
        try:
            # ── Step 1: List VMs (sanity check) ───────────────
            log.info("Step 1: Listing VMs on node '%s'...", NODE)
            vms = await pve.list_vms(NODE)
            log.info("  Found %d VMs", len(vms))
            template = next((v for v in vms if v.get("vmid") == TEMPLATE_VMID), None)
            if template is None:
                log.error("  Template VMID %d not found on node '%s'", TEMPLATE_VMID, NODE)
                return 1
            log.info("  Template found: %s (vmid=%d)", template.get("name", "?"), TEMPLATE_VMID)

            # ── Step 2: Clone ─────────────────────────────────
            log.info("Step 2: Cloning template %d -> %d (%s)...", TEMPLATE_VMID, CLONE_VMID, CLONE_NAME)
            upid = await pve.clone_vm(
                node=NODE,
                template_vmid=TEMPLATE_VMID,
                new_vmid=CLONE_VMID,
                name=CLONE_NAME,
            )
            log.info("  Clone task: %s", upid)
            log.info("  Waiting for clone to complete (timeout=600s)...")
            await pve.wait_for_task(NODE, upid, timeout=600)
            log.info("  Clone completed!")

            # ── Step 3: Start VM ──────────────────────────────
            log.info("Step 3: Starting VM %d...", CLONE_VMID)
            upid = await pve.start_vm(NODE, CLONE_VMID)
            await pve.wait_for_task(NODE, upid, timeout=60)
            log.info("  VM started!")

            # ── Step 4: Check status ──────────────────────────
            log.info("Step 4: Checking VM status...")
            status = await pve.get_vm_status(NODE, CLONE_VMID)
            log.info("  Status: %s  QMP: %s  CPUs: %s  Mem: %s MB",
                     status.get("status"),
                     status.get("qmpstatus"),
                     status.get("cpus"),
                     (status.get("maxmem", 0) or 0) // (1024 * 1024))

            # ── Step 5: Wait for guest agent ──────────────────
            log.info("Step 5: Waiting for QEMU guest agent (up to 120s)...")
            agent_ready = False
            for i in range(60):
                if await pve.agent_ping(NODE, CLONE_VMID):
                    agent_ready = True
                    break
                if i % 10 == 0 and i > 0:
                    log.info("  Still waiting... (%ds)", i * 2)
                await asyncio.sleep(2)

            if agent_ready:
                log.info("  Guest agent responding!")

                # ── Step 5b: Get users ────────────────────────
                log.info("  Querying logged-in OS users...")
                users = await pve.agent_get_users(NODE, CLONE_VMID)
                if users:
                    for u in users:
                        log.info("    User: %s", u.get("user", "?"))
                else:
                    log.info("    No users logged in (expected for fresh clone)")
            else:
                log.warning("  Guest agent not responding after 120s (VM may not have qemu-guest-agent installed)")

            # ── Step 6: VNC ticket ────────────────────────────
            log.info("Step 6: Getting VNC proxy ticket...")
            vnc = await pve.get_vnc_ticket(NODE, CLONE_VMID)
            log.info("  VNC port: %s", vnc.get("port"))
            log.info("  VNC ticket: %s...", str(vnc.get("ticket", ""))[:40])
            log.info("  Connect URL: wss://%s:%s/websockify?token=%s",
                     settings.pve_api_url.split("//")[1].split(":")[0],
                     vnc.get("port"),
                     vnc.get("ticket", "")[:20] + "...")

            # ── Step 7: Stop & Destroy ────────────────────────
            log.info("Step 7: Stopping VM %d...", CLONE_VMID)
            upid = await pve.stop_vm(NODE, CLONE_VMID)
            await pve.wait_for_task(NODE, upid, timeout=60)
            log.info("  VM stopped!")

            log.info("Step 8: Destroying VM %d...", CLONE_VMID)
            upid = await pve.destroy_vm(NODE, CLONE_VMID)
            await pve.wait_for_task(NODE, upid, timeout=60)
            log.info("  VM destroyed!")

            log.info("=== ALL TESTS PASSED ===")
            return 0

        except ProxmoxError as exc:
            log.error("Proxmox API error: %s", exc)
            # Attempt cleanup
            await _cleanup(pve, NODE, CLONE_VMID)
            return 1
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)
            await _cleanup(pve, NODE, CLONE_VMID)
            return 1


async def _cleanup(pve: ProxmoxClient, node: str, vmid: int):
    """Best-effort cleanup of the test VM."""
    log.info("Attempting cleanup of VM %d...", vmid)
    try:
        status = await pve.get_vm_status(node, vmid)
        if status.get("status") == "running":
            upid = await pve.stop_vm(node, vmid)
            await pve.wait_for_task(node, upid, timeout=30)
        upid = await pve.destroy_vm(node, vmid)
        await pve.wait_for_task(node, upid, timeout=30)
        log.info("  Cleanup succeeded")
    except Exception as exc:
        log.warning("  Cleanup failed (manual removal may be needed): %s", exc)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

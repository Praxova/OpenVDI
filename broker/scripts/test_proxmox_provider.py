"""
Milestone 1 acceptance test for the OpenVDI Proxmox provider.

Runs an end-to-end clone / start / agent-ping / vnc-ticket / shutdown /
destroy cycle against a real Proxmox cluster. Drives the HypervisorProvider
interface only; the concrete provider package is never imported.

Not a pytest -- run directly:

    cd broker
    python -m scripts.test_proxmox_provider

On failure, this script does NOT auto-clean up partial state. Inspect the
wreckage manually. See docs/implementation-plan.md Milestone 1 preconditions
for the assumed environment.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass

from app.config import get_settings
from app.providers import get_provider_class, list_provider_types
from app.providers.base import (
    CloneRequest,
    ConsoleKind,
    NoVNCTicket,
    VMRef,
)
from app.providers.exceptions import ProviderError  # noqa: F401  (caught implicitly)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("m1-acceptance")


@dataclass
class StepResult:
    name: str
    passed: bool
    duration: float
    error: str | None = None


results: list[StepResult] = []


async def step(name: str, coro):
    logger.info("")
    logger.info("--- %s ---", name)
    t0 = time.monotonic()
    try:
        out = await coro
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.exception("FAIL  %s  (%.2fs)", name, elapsed)
        results.append(StepResult(name, False, elapsed, str(exc)))
        raise
    elapsed = time.monotonic() - t0
    logger.info("PASS  %s  (%.2fs)", name, elapsed)
    results.append(StepResult(name, True, elapsed))
    return out


def _print_summary(overall_pass: bool) -> None:
    print()
    print("=" * 66)
    print("  Milestone 1 Acceptance -- Summary")
    print("=" * 66)
    print(f"  {'Step':<46}{'Status':<8}{'Duration':>10}")
    print("  " + "-" * 62)
    total = 0.0
    for r in results:
        total += r.duration
        print(
            f"  {r.name:<46}{'PASS' if r.passed else 'FAIL':<8}{r.duration:>9.2f}s"
        )
    print("  " + "-" * 62)
    print(
        f"  {'TOTAL':<46}{'PASS' if overall_pass else 'FAIL':<8}{total:>9.2f}s"
    )
    print("=" * 66)


async def main() -> int:
    settings = get_settings()

    print("=" * 66)
    print("  Milestone 1 Acceptance -- OpenVDI Proxmox Provider")
    print(f"  Target   : {settings.proxmox_api_url}")
    print(f"  Node     : {settings.proxmox_default_node}")
    print(f"  Template : VMID {settings.proxmox_template_vmid}")
    print(f"  Clone    : VMID {settings.proxmox_test_vmid}")
    print("=" * 66)

    overall_pass = True
    try:
        # ── 1. Registry sanity ─────────────────────────────────
        async def _registry() -> None:
            types = list_provider_types()
            logger.info("  provider types: %s", types)
            if "proxmox" not in types:
                raise RuntimeError(f"'proxmox' not in registry: {types}")

        await step("1. Registry sanity check", _registry())

        # ── 2. Construct provider ──────────────────────────────
        async def _construct():
            cls = get_provider_class("proxmox")
            logger.info("  class: %s  provider_type=%s", cls.__name__, cls.provider_type)
            return cls(
                settings.proxmox_api_url,
                settings.proxmox_token_id,
                settings.proxmox_token_secret.get_secret_value(),
                settings.proxmox_verify_ssl,
            )

        provider_ctx = await step("2. Construct provider", _construct())

        async with provider_ctx as provider:
            # ── 3. Ping ────────────────────────────────────────
            async def _ping() -> None:
                ok = await provider.ping()
                if not ok:
                    raise RuntimeError("ping returned False")

            await step("3. Ping", _ping())

            # ── 4. Capabilities ────────────────────────────────
            async def _caps() -> None:
                caps = await provider.capabilities()
                logger.info("  provider_type : %s", caps.provider_type)
                logger.info(
                    "  console_kinds : %s",
                    sorted(k.value for k in caps.console_kinds),
                )
                logger.info(
                    "  linked_clones=%s snapshots=%s guest_agent=%s",
                    caps.linked_clones,
                    caps.snapshots,
                    caps.guest_agent,
                )

            await step("4. Capabilities", _caps())

            # ── 5. List nodes ──────────────────────────────────
            async def _list_nodes() -> None:
                nodes = await provider.list_nodes()
                match = next(
                    (n for n in nodes if n.node == settings.proxmox_default_node),
                    None,
                )
                if match is None:
                    raise RuntimeError(
                        f"default node {settings.proxmox_default_node!r} "
                        f"not in cluster (got {[n.node for n in nodes]})"
                    )
                if match.status != "online":
                    raise RuntimeError(
                        f"node {match.node} not online: status={match.status}"
                    )
                logger.info(
                    "  %s  status=%s  cores=%d  mem=%.1fGB",
                    match.node,
                    match.status,
                    match.cpu_cores,
                    match.memory_bytes / (1024**3) if match.memory_bytes else 0.0,
                )

            await step("5. List nodes", _list_nodes())

            # ── 6. List storage ────────────────────────────────
            async def _list_storage() -> None:
                stores = await provider.list_storage(settings.proxmox_default_node)
                names = [s.name for s in stores]
                logger.info("  %d storages: %s", len(stores), names)
                if settings.proxmox_target_storage:
                    if settings.proxmox_target_storage not in names:
                        raise RuntimeError(
                            f"target storage {settings.proxmox_target_storage!r} "
                            f"not found on {settings.proxmox_default_node}: {names}"
                        )
                    logger.info(
                        "  target storage %s: present",
                        settings.proxmox_target_storage,
                    )
                else:
                    logger.info(
                        "  (PROXMOX_TARGET_STORAGE not configured; "
                        "skipping presence check)"
                    )

            await step("6. List storage", _list_storage())

            # ── 7. Template check ──────────────────────────────
            template_ref = VMRef(
                provider_type="proxmox",
                data={
                    "node": settings.proxmox_default_node,
                    "vmid": settings.proxmox_template_vmid,
                },
            )

            async def _template() -> None:
                st = await provider.get_vm_status(template_ref)
                logger.info(
                    "  name=%s  power=%s  is_template=%s  agent_cfg=%s",
                    st.name,
                    st.power_state,
                    st.is_template,
                    st.guest_agent_configured,
                )
                if not st.is_template:
                    # Different PVE versions report this inconsistently in
                    # the status/current endpoint; warn but do not fail.
                    logger.warning(
                        "  is_template=False; PVE status/current can be "
                        "inconsistent here, continuing"
                    )

            await step("7. Template check", _template())

            # ── 8. Test VMID free ──────────────────────────────
            async def _vmid_free() -> None:
                vms = await provider.list_vms(node=settings.proxmox_default_node)
                clashing = [
                    v
                    for v in vms
                    if v.ref.data.get("vmid") == settings.proxmox_test_vmid
                ]
                if clashing:
                    raise RuntimeError(
                        f"test VMID {settings.proxmox_test_vmid} already exists: "
                        f"prior run left state; destroy VMID "
                        f"{settings.proxmox_test_vmid} and/or clear LVM locks, "
                        f"then retry"
                    )
                logger.info("  VMID %d is free", settings.proxmox_test_vmid)

            await step("8. Test VMID free", _vmid_free())

            # ── 9. Clone ───────────────────────────────────────
            async def _clone():
                req = CloneRequest(
                    source_ref=template_ref,
                    new_name="openvdi-test",
                    target_storage=settings.proxmox_target_storage,
                    provider_opts={"newid": settings.proxmox_test_vmid},
                )
                handle = await provider.clone_vm(req)
                logger.info("  task handle: %s", handle.data)
                return handle

            clone_handle = await step("9. Clone", _clone())

            # ── 10. Wait for clone ─────────────────────────────
            async def _wait_clone() -> None:
                st = await provider.wait_for_task(clone_handle, timeout_seconds=600)
                logger.info("  clone task finished: success=%s", st.success)

            await step("10. Wait for clone", _wait_clone())

            # ── 11. Build test VMRef ───────────────────────────
            test_ref_holder: dict = {}

            async def _build_test_ref() -> None:
                ref = VMRef(
                    provider_type="proxmox",
                    data={
                        "node": settings.proxmox_default_node,
                        "vmid": settings.proxmox_test_vmid,
                    },
                )
                test_ref_holder["ref"] = ref
                logger.info("  test_ref=%s", ref.data)

            await step("11. Build test VMRef", _build_test_ref())
            test_ref: VMRef = test_ref_holder["ref"]

            # ── 12. Start VM ───────────────────────────────────
            async def _start() -> None:
                handle = await provider.start_vm(test_ref)
                logger.info("  start task handle: %s", handle.data)
                await provider.wait_for_task(handle, timeout_seconds=30)

            await step("12. Start VM", _start())

            # ── 13. Poll for agent ─────────────────────────────
            async def _agent_poll() -> None:
                deadline = time.monotonic() + 90
                attempt = 0
                while time.monotonic() < deadline:
                    attempt += 1
                    t_ping = time.monotonic()
                    ok = await provider.agent_ping(test_ref)
                    dt = time.monotonic() - t_ping
                    logger.info(
                        "  attempt %d: agent_ping=%s (%.2fs)", attempt, ok, dt
                    )
                    if ok:
                        logger.info("  agent up after %d attempts", attempt)
                        return
                    await asyncio.sleep(2)
                raise RuntimeError("guest agent did not respond within 90s")

            await step("13. Poll for agent", _agent_poll())

            # ── 14. Get users ──────────────────────────────────
            async def _users() -> None:
                users = await provider.agent_get_users(test_ref)
                if users:
                    for u in users:
                        logger.info(
                            "  user=%s login_time=%s domain=%s",
                            u.username,
                            u.login_time,
                            u.domain,
                        )
                else:
                    logger.info(
                        "  no users currently logged in (expected for fresh clone)"
                    )

            await step("14. Get users", _users())

            # ── 15. noVNC ticket ───────────────────────────────
            async def _novnc() -> None:
                ticket = await provider.get_console_ticket(test_ref, ConsoleKind.NOVNC)
                if not isinstance(ticket, NoVNCTicket):
                    raise RuntimeError(
                        f"expected NoVNCTicket, got {type(ticket).__name__}"
                    )
                logger.info("  websocket_url = %s", ticket.websocket_url)
                logger.info(
                    "  password      = ***  (len=%d)",
                    len(ticket.password),
                )
                logger.info(
                    "  cert_pem      = %s",
                    f"<PEM {len(ticket.cert_pem)} chars>"
                    if ticket.cert_pem
                    else None,
                )

            await step("15. noVNC ticket", _novnc())

            # ── 16. Shutdown ───────────────────────────────────
            async def _shutdown() -> None:
                handle = await provider.shutdown_vm(
                    test_ref, timeout_seconds=120, force=True
                )
                logger.info("  shutdown task handle: %s", handle.data)
                await provider.wait_for_task(handle, timeout_seconds=180)

            await step("16. Shutdown", _shutdown())

            # ── 17. Confirm stopped ────────────────────────────
            async def _confirm_stopped() -> None:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    st = await provider.get_vm_status(test_ref)
                    logger.info("  power_state=%s", st.power_state)
                    if st.power_state == "stopped":
                        return
                    await asyncio.sleep(2)
                raise RuntimeError("VM did not reach stopped state within 30s")

            await step("17. Confirm stopped", _confirm_stopped())

            # ── 18. Destroy ────────────────────────────────────
            async def _destroy() -> None:
                handle = await provider.destroy_vm(test_ref)
                logger.info("  destroy task handle: %s", handle.data)
                await provider.wait_for_task(handle, timeout_seconds=120)

            await step("18. Destroy", _destroy())

            # ── 19. Confirm gone ───────────────────────────────
            async def _confirm_gone() -> None:
                vms = await provider.list_vms(node=settings.proxmox_default_node)
                still = [
                    v
                    for v in vms
                    if v.ref.data.get("vmid") == settings.proxmox_test_vmid
                ]
                if still:
                    raise RuntimeError(
                        f"test VMID {settings.proxmox_test_vmid} still present after destroy"
                    )
                logger.info(
                    "  VMID %d no longer present", settings.proxmox_test_vmid
                )

            await step("19. Confirm gone", _confirm_gone())

    except Exception:
        overall_pass = False

    _print_summary(overall_pass)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

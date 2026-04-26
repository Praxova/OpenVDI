"""Mock-provider acceptance test for task_tracker + provisioner rebuild path.

Holds us over until the VDI-specific Proxmox template is ready; covers the
code paths the real-hypervisor tests would hit:

  1. provisioner existing_desktop path — VMID / node / name preserved,
     row reaches AVAILABLE, quiesce is patched to 0s for speed.
  2. start_desktop_task + poll_desktop_task end-to-end for DESTROY —
     Desktop row deleted, audit event logged.
  3. resume_inflight_tasks — four sub-cases:
       3a. orphan UPID + valid kind → poller runs to completion
       3b. UPID present, kind NULL → row transitions to ERROR
       3c. UPID present, kind is not a known DesktopTaskKind → ERROR
       3d. UPID + kind present, cluster has no provider entry → ERROR

Run directly (not pytest):

    cd broker
    ../.venv/bin/python -m scripts.test_task_tracker_mock

The script writes and cleans up its own Desktop + AuditLog rows against the
seeded `engineering` pool. It does NOT touch the Proxmox cluster.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import types
import uuid
from dataclasses import dataclass
from typing import Any, ClassVar

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.database import async_session_factory, dispose_engine
from app.models import AuditLog, Cluster, Desktop, DesktopStatus, Pool, Template
from app.models.pool import PoolType
from app.providers.base import (
    CloneRequest,
    ConsoleKind,
    ConsoleTicket,
    ProviderCapabilities,
    TaskHandle,
    TaskStatus,
    VMConfig,
    VMRef,
    VMStatus,
)
from app.providers.exceptions import ProviderTaskError, ProviderTimeoutError
from app.services import provisioner as provisioner_mod
from app.services.provisioner import provision_desktop
from app.services.task_tracker import (
    DesktopTaskKind,
    poll_desktop_task,
    resume_inflight_tasks,
    start_desktop_task,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logger = logging.getLogger("task-tracker-mock")


# ── Mock provider ─────────────────────────────────────────────


class MockProvider:
    """In-memory HypervisorProvider. Mock-only; tests own the outcomes.

    Tasks default to success. Per-UPID overrides via `self.task_outcomes`:
        "success" | "failure" | "timeout"
    """

    provider_type: ClassVar[str] = "proxmox"

    def __init__(self) -> None:
        self._upid_counter = 0
        self.task_outcomes: dict[str, str] = {}
        self.agent_responses: dict[int, bool] = {}
        self.default_agent_response = True
        self.calls: list[tuple[str, Any]] = []

    def _next_upid(self, kind: str) -> str:
        self._upid_counter += 1
        return f"UPID:mock:0000{self._upid_counter:04x}:00:00:mock-{kind}:::"

    def _handle(self, node: str, kind: str) -> TaskHandle:
        return TaskHandle(
            provider_type=self.provider_type,
            data={"node": node, "upid": self._next_upid(kind)},
        )

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=self.provider_type,
            linked_clones=True, full_clones=True, snapshots=True,
            guest_agent=True, live_migration=False,
            console_kinds=frozenset({ConsoleKind.NOVNC}),
            supports_pool_tags=True, supports_resource_pools=True,
        )

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def clone_vm(self, req: CloneRequest) -> TaskHandle:
        self.calls.append(("clone_vm", req))
        node = req.target_node or req.source_ref.data.get("node", "mock")
        return self._handle(node, "clone")

    async def start_vm(self, ref: VMRef) -> TaskHandle:
        self.calls.append(("start_vm", ref))
        return self._handle(ref.data["node"], "start")

    async def stop_vm(self, ref: VMRef) -> TaskHandle:
        self.calls.append(("stop_vm", ref))
        return self._handle(ref.data["node"], "stop")

    async def shutdown_vm(
        self, ref: VMRef, timeout_seconds: int = 120, force: bool = False,
    ) -> TaskHandle:
        self.calls.append(("shutdown_vm", ref))
        return self._handle(ref.data["node"], "shutdown")

    async def reboot_vm(self, ref: VMRef) -> TaskHandle:
        self.calls.append(("reboot_vm", ref))
        return self._handle(ref.data["node"], "reboot")

    async def destroy_vm(self, ref: VMRef, purge: bool = True) -> TaskHandle:
        self.calls.append(("destroy_vm", ref))
        return self._handle(ref.data["node"], "destroy")

    async def configure_vm(
        self, ref: VMRef, config: VMConfig,
    ) -> TaskHandle | None:
        self.calls.append(("configure_vm", (ref, config)))
        return self._handle(ref.data["node"], "configure")

    async def create_snapshot(
        self, ref: VMRef, name: str,
        description: str | None = None, include_ram: bool = False,
    ) -> TaskHandle:
        self.calls.append(("create_snapshot", (ref, name)))
        return self._handle(ref.data["node"], "snapshot")

    async def rollback_snapshot(self, ref: VMRef, name: str) -> TaskHandle:
        self.calls.append(("rollback_snapshot", (ref, name)))
        return self._handle(ref.data["node"], "rollback")

    async def delete_snapshot(self, ref: VMRef, name: str) -> TaskHandle:
        self.calls.append(("delete_snapshot", (ref, name)))
        return self._handle(ref.data["node"], "snapdel")

    async def list_snapshots(self, ref: VMRef) -> list:
        return []

    async def get_vm_status(self, ref: VMRef) -> VMStatus:
        raise NotImplementedError

    async def list_vms(self, node: str | None = None) -> list[VMStatus]:
        return []

    async def list_nodes(self) -> list:
        return []

    async def get_node_status(self, node: str):
        raise NotImplementedError

    async def list_storage(self, node: str) -> list:
        return []

    async def agent_ping(self, ref: VMRef) -> bool:
        vmid = ref.data["vmid"]
        return self.agent_responses.get(vmid, self.default_agent_response)

    async def agent_get_users(self, ref: VMRef) -> list:
        return []

    async def agent_get_osinfo(self, ref: VMRef):
        raise NotImplementedError

    async def agent_get_network(self, ref: VMRef) -> list:
        return []

    async def agent_exec(
        self, ref: VMRef, command: list[str], input_data: str | None = None,
    ) -> int:
        return 1

    async def agent_exec_status(self, ref: VMRef, pid: int):
        raise NotImplementedError

    async def get_console_ticket(
        self, ref: VMRef, kind: ConsoleKind,
    ) -> ConsoleTicket:
        raise NotImplementedError

    async def get_task_status(self, handle: TaskHandle) -> TaskStatus:
        return TaskStatus(
            state="stopped", success=True, error_message=None, raw={},
        )

    async def wait_for_task(
        self, handle: TaskHandle,
        timeout_seconds: int = 600, poll_interval: float = 1.0,
    ) -> TaskStatus:
        upid = handle.data["upid"]
        outcome = self.task_outcomes.get(upid, "success")
        if outcome == "success":
            return TaskStatus(
                state="stopped", success=True, error_message=None,
                raw={"upid": upid},
            )
        if outcome == "failure":
            raise ProviderTaskError(f"mock task {upid} failed")
        if outcome == "timeout":
            raise ProviderTimeoutError(
                f"mock task {upid} timed out (s={timeout_seconds})"
            )
        raise RuntimeError(f"bad mock outcome {outcome!r}")


# ── Test harness ──────────────────────────────────────────────


@dataclass
class StepResult:
    name: str
    passed: bool
    error: str | None = None


results: list[StepResult] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        logger.info("  PASS: %s", name)
    else:
        logger.error("  FAIL: %s — %s", name, detail)
        raise AssertionError(f"{name}: {detail}")


async def step(name: str, coro) -> None:
    logger.info("")
    logger.info("=== %s ===", name)
    try:
        await coro
        results.append(StepResult(name=name, passed=True))
    except Exception as exc:
        logger.exception("step failed: %s", name)
        results.append(StepResult(name=name, passed=False, error=str(exc)))


# Fixture names this script owns. _cleanup_fixtures targets only these,
# so unrelated rows left by other flows are untouched.
_TEST_POOL_NAME = "task-tracker-mock-pool"
_TEST_TEMPLATE_NAME = "task-tracker-mock-template"
_TEST_VMID_MIN = 5090
_TEST_VMID_MAX = 5099


async def _cleanup_fixtures() -> None:
    """Purge any Desktop/Pool/Template rows left over from prior runs.

    Scoped to the fixture names + vmid sandbox this script owns so we
    don't clobber data created by other flows.
    """
    async with async_session_factory() as session:
        await session.execute(
            delete(Desktop).where(
                Desktop.pve_vmid.between(_TEST_VMID_MIN, _TEST_VMID_MAX)
            )
        )
        await session.execute(
            delete(Pool).where(Pool.name == _TEST_POOL_NAME)
        )
        await session.execute(
            delete(Template).where(Template.name == _TEST_TEMPLATE_NAME)
        )
        await session.commit()


async def _ensure_test_fixtures() -> tuple[Pool, Template, Cluster]:
    """Create a cluster+template+pool we control for this script.

    Uses the placeholder cluster from 002_seed_data.sql. Called once
    from main() after _cleanup_fixtures; each scenario looks up the
    fixtures fresh via _get_test_fixtures().
    """
    async with async_session_factory() as session:
        # Placeholder cluster id, seeded by 002_seed_data.sql.
        cluster = (
            await session.execute(
                select(Cluster).where(Cluster.name == "default")
            )
        ).scalar_one()

        template = Template(
            cluster_id=cluster.id,
            name=_TEST_TEMPLATE_NAME,
            pve_vmid=8999,
            pve_node="mocknode",
            os_type="windows11",
            cpu_cores=2,
            memory_mb=4096,
            disk_gb=40,
        )
        session.add(template)
        await session.flush()

        pool = Pool(
            name=_TEST_POOL_NAME,
            display_name="Mock pool for task_tracker tests",
            pool_type=PoolType.PERSISTENT,
            template_id=template.id,
            cluster_id=cluster.id,
            min_spare=0,
            max_size=10,  # 5090..5099 inclusive = 10 slots (schema CHECK)
            vmid_range_start=_TEST_VMID_MIN,
            vmid_range_end=_TEST_VMID_MAX,
            name_prefix="ENG",
            refresh_on_logoff=False,
        )
        session.add(pool)
        await session.commit()
        await session.refresh(pool)
        await session.refresh(template)
        await session.refresh(cluster)
        # Detach so callers can use these outside the session without
        # tripping MissingGreenlet on lazy-load attempts.
        session.expunge_all()
    return pool, template, cluster


async def _get_test_fixtures() -> tuple[Pool, Template, Cluster]:
    """Re-read the fixtures created by `_ensure_test_fixtures`.

    Each scenario calls this so it has a freshly-loaded, session-
    independent copy of the rows.
    """
    async with async_session_factory() as session:
        pool = (
            await session.execute(
                select(Pool)
                .where(Pool.name == _TEST_POOL_NAME)
                .options(selectinload(Pool.template))
            )
        ).scalar_one()
        template = pool.template
        cluster = await session.get(Cluster, pool.cluster_id)
        session.expunge_all()
    return pool, template, cluster


def _fake_app(providers: dict[uuid.UUID, Any]) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            providers=providers,
            task_tracker_tasks=set(),
        ),
    )


# ── Scenarios ─────────────────────────────────────────────────


async def scenario_existing_desktop() -> None:
    """provision_desktop(existing_desktop=...) preserves vmid/node/name
    and reaches AVAILABLE. Non-persistent pool path is skipped — the
    engineering pool is persistent, which exercises the happy path
    without the post-boot quiesce + snapshot dance."""
    pool, template, _cluster = await _get_test_fixtures()
    provider = MockProvider()

    original_vmid = 5090
    original_node = template.pve_node
    original_name = "ENG-091"

    async with async_session_factory() as session:
        pool_fresh = await session.get(Pool, pool.id, populate_existing=True)
        template_fresh = await session.get(Template, template.id)
        row = Desktop(
            pool_id=pool_fresh.id,
            pve_vmid=original_vmid,
            pve_node=original_node,
            name=original_name,
            status=DesktopStatus.ERROR,
            error_message="pre-existing error — about to rebuild",
            power_state="stopped",
            assigned_user="alice",
            assignment_type="persistent",
        )
        session.add(row)
        await session.commit()
        desktop_id = row.id

    async with async_session_factory() as session:
        existing = await session.get(Desktop, desktop_id)
        pool_in = await session.get(Pool, pool.id)
        template_in = await session.get(Template, template.id)
        returned = await provision_desktop(
            session=session,
            provider=provider,
            pool=pool_in,
            template=template_in,
            assigned_user="alice",
            existing_desktop=existing,
        )
        await session.refresh(returned)

    check("row reused (same id)", returned.id == desktop_id,
          f"expected {desktop_id}, got {returned.id}")
    check("vmid preserved", returned.pve_vmid == original_vmid,
          f"expected {original_vmid}, got {returned.pve_vmid}")
    check("node preserved", returned.pve_node == original_node,
          f"expected {original_node!r}, got {returned.pve_node!r}")
    check("name preserved", returned.name == original_name,
          f"expected {original_name!r}, got {returned.name!r}")
    check("status=ASSIGNED (persistent + assigned_user)",
          returned.status == DesktopStatus.ASSIGNED,
          f"status={returned.status}")
    check("error_message cleared",
          returned.error_message is None,
          f"error_message={returned.error_message!r}")
    check("pve_task_upid cleared on success",
          returned.pve_task_upid is None,
          f"pve_task_upid={returned.pve_task_upid!r}")
    check("pve_task_kind cleared on success",
          returned.pve_task_kind is None,
          f"pve_task_kind={returned.pve_task_kind!r}")
    check("provider saw clone_vm", any(c[0] == "clone_vm" for c in provider.calls))
    check("provider saw configure_vm", any(c[0] == "configure_vm" for c in provider.calls))
    check("provider saw start_vm", any(c[0] == "start_vm" for c in provider.calls))


async def scenario_destroy_via_task_tracker() -> None:
    """start_desktop_task queues the poll; poll completes; row deleted;
    audit event present."""
    pool, template, cluster = await _get_test_fixtures()
    provider = MockProvider()

    async with async_session_factory() as session:
        row = Desktop(
            pool_id=pool.id,
            pve_vmid=5091,
            pve_node=template.pve_node,
            name="ENG-092",
            status=DesktopStatus.ASSIGNED,
            assigned_user="bob",
            assignment_type="persistent",
            power_state="stopped",
        )
        session.add(row)
        await session.commit()
        desktop_id = row.id

    # start_desktop_task needs desktop.pool loaded (for cluster_id lookup).
    # We also skip real BackgroundTasks: call poll_desktop_task directly.
    async with async_session_factory() as session:
        desktop = (
            await session.execute(
                select(Desktop).where(Desktop.id == desktop_id)
                .options(selectinload(Desktop.pool))
            )
        ).scalar_one()

        handle = await provider.destroy_vm(
            VMRef(provider_type="proxmox",
                  data={"node": desktop.pve_node, "vmid": desktop.pve_vmid}),
        )

        class _FakeBG:
            def __init__(self):
                self.queued: list = []
            def add_task(self, fn, *args, **kwargs):
                self.queued.append((fn, args, kwargs))

        bg = _FakeBG()
        await start_desktop_task(
            session=session,
            desktop=desktop,
            kind=DesktopTaskKind.DESTROY,
            task_handle=handle,
            background_tasks=bg,
        )
        await session.commit()

        check("task queued", len(bg.queued) == 1,
              f"expected 1 queued task, got {len(bg.queued)}")
        fn, args, _kw = bg.queued[0]

    # provider_factory captured by start_desktop_task imports app.main.
    # Substitute our mock provider into app.state.providers under the
    # real cluster id.
    from app.main import app as main_app
    original = main_app.state.providers.get(cluster.id) if hasattr(main_app.state, "providers") else None
    if not hasattr(main_app.state, "providers"):
        main_app.state.providers = {}
    main_app.state.providers[cluster.id] = provider
    try:
        await fn(*args)
    finally:
        if original is not None:
            main_app.state.providers[cluster.id] = original
        else:
            main_app.state.providers.pop(cluster.id, None)

    # Row should be gone, audit row present.
    async with async_session_factory() as session:
        still_there = await session.get(Desktop, desktop_id)
        audit = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.resource_id == desktop_id)
                .where(AuditLog.action == "desktop.destroy.completed")
            )
        ).scalars().first()

    check("desktop row deleted after destroy",
          still_there is None, "row still present")
    check("audit row written", audit is not None, "no audit row found")


async def _seed_orphan_desktop(
    *, vmid: int, name: str,
    pool_id: uuid.UUID, pve_node: str,
    upid: str | None, kind: str | None,
) -> uuid.UUID:
    async with async_session_factory() as session:
        row = Desktop(
            pool_id=pool_id,
            pve_vmid=vmid,
            pve_node=pve_node,
            name=name,
            status=DesktopStatus.PROVISIONING,
            power_state="stopped",
            pve_task_upid=upid,
            pve_task_kind=kind,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _drain(app: types.SimpleNamespace) -> None:
    tasks = list(app.state.task_tracker_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def scenario_resume_orphan_valid() -> None:
    """Orphan DESTROY UPID + valid kind: resume spawns poller, row deleted."""
    pool, template, cluster = await _get_test_fixtures()
    provider = MockProvider()
    upid = provider._next_upid("destroy")  # reserve a UPID the provider knows
    desktop_id = await _seed_orphan_desktop(
        vmid=5092, name="ENG-093",
        pool_id=pool.id, pve_node=template.pve_node,
        upid=upid, kind=DesktopTaskKind.DESTROY.value,
    )

    fake_app = _fake_app({cluster.id: provider})
    await resume_inflight_tasks(fake_app)
    check("resume spawned one task",
          len(fake_app.state.task_tracker_tasks) == 1,
          f"spawned {len(fake_app.state.task_tracker_tasks)}")
    await _drain(fake_app)

    async with async_session_factory() as session:
        still_there = await session.get(Desktop, desktop_id)
    check("orphan DESTROY row removed after resume",
          still_there is None, "row still present")


async def scenario_resume_null_kind() -> None:
    """UPID present, kind NULL → ERROR; no poller spawned."""
    pool, template, _cluster = await _get_test_fixtures()
    desktop_id = await _seed_orphan_desktop(
        vmid=5093, name="ENG-094",
        pool_id=pool.id, pve_node=template.pve_node,
        upid="UPID:mock:orphan:null-kind",
        kind=None,
    )

    # Providers map can even be empty — the null-kind branch never needs one.
    fake_app = _fake_app({})
    await resume_inflight_tasks(fake_app)
    check("no poller spawned for null-kind row",
          len(fake_app.state.task_tracker_tasks) == 0,
          f"spawned {len(fake_app.state.task_tracker_tasks)}")

    async with async_session_factory() as session:
        row = await session.get(Desktop, desktop_id)
    check("row transitioned to ERROR",
          row is not None and row.status == DesktopStatus.ERROR,
          f"status={row.status if row else None}")
    check("error_message mentions unknown kind",
          row is not None and row.error_message is not None
          and "unknown" in row.error_message.lower(),
          f"error_message={row.error_message if row else None!r}")
    check("task fields cleared",
          row is not None and row.pve_task_upid is None and row.pve_task_kind is None,
          f"upid={row.pve_task_upid if row else None!r} kind={row.pve_task_kind if row else None!r}")


async def scenario_resume_unknown_kind() -> None:
    """UPID present, kind is a random string → ERROR."""
    pool, template, _cluster = await _get_test_fixtures()
    desktop_id = await _seed_orphan_desktop(
        vmid=5094, name="ENG-095",
        pool_id=pool.id, pve_node=template.pve_node,
        upid="UPID:mock:orphan:unknown-kind",
        kind="snorkel",
    )

    fake_app = _fake_app({})
    await resume_inflight_tasks(fake_app)
    check("no poller spawned for unknown-kind row",
          len(fake_app.state.task_tracker_tasks) == 0,
          f"spawned {len(fake_app.state.task_tracker_tasks)}")

    async with async_session_factory() as session:
        row = await session.get(Desktop, desktop_id)
    check("row transitioned to ERROR",
          row is not None and row.status == DesktopStatus.ERROR,
          f"status={row.status if row else None}")
    check("error_message mentions unknown kind",
          row is not None and row.error_message is not None
          and "unknown" in row.error_message.lower()
          and "snorkel" in row.error_message,
          f"error_message={row.error_message if row else None!r}")


async def scenario_resume_offline_provider() -> None:
    """Valid kind but no provider for the cluster → poller runs,
    provider_factory raises, row transitions to ERROR with
    'provider unavailable'."""
    pool, template, _cluster = await _get_test_fixtures()
    desktop_id = await _seed_orphan_desktop(
        vmid=5095, name="ENG-096",
        pool_id=pool.id, pve_node=template.pve_node,
        upid="UPID:mock:orphan:offline-provider",
        kind=DesktopTaskKind.DESTROY.value,
    )

    # Empty providers map — the factory will raise.
    fake_app = _fake_app({})
    await resume_inflight_tasks(fake_app)
    check("poller was spawned (kind was valid)",
          len(fake_app.state.task_tracker_tasks) == 1,
          f"spawned {len(fake_app.state.task_tracker_tasks)}")
    await _drain(fake_app)

    async with async_session_factory() as session:
        row = await session.get(Desktop, desktop_id)
    check("row transitioned to ERROR",
          row is not None and row.status == DesktopStatus.ERROR,
          f"status={row.status if row else None}")
    check("error_message mentions provider unavailable",
          row is not None and row.error_message is not None
          and "provider unavailable" in row.error_message,
          f"error_message={row.error_message if row else None!r}")
    check("task fields cleared",
          row is not None and row.pve_task_upid is None and row.pve_task_kind is None,
          f"upid={row.pve_task_upid if row else None!r} kind={row.pve_task_kind if row else None!r}")


# ── Entry ─────────────────────────────────────────────────────


async def main() -> int:
    # Collapse the non-persistent quiesce so the test isn't wall-clock gated;
    # the engineering pool is persistent so this is belt-and-suspenders.
    provisioner_mod._POST_BOOT_QUIESCE_SECONDS = 0

    await _cleanup_fixtures()
    await _ensure_test_fixtures()

    try:
        await step("1. existing_desktop path reaches AVAILABLE",
                   scenario_existing_desktop())
        await step("2. destroy via start_desktop_task + poll_desktop_task",
                   scenario_destroy_via_task_tracker())
        await step("3a. resume orphan UPID + valid kind",
                   scenario_resume_orphan_valid())
        await step("3b. resume with NULL kind",
                   scenario_resume_null_kind())
        await step("3c. resume with unknown kind",
                   scenario_resume_unknown_kind())
        await step("3d. resume with offline provider",
                   scenario_resume_offline_provider())
    finally:
        await _cleanup_fixtures()
        await dispose_engine()

    logger.info("")
    logger.info("─" * 60)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    for r in results:
        mark = "OK  " if r.passed else "FAIL"
        logger.info("%s  %s%s", mark, r.name, f" — {r.error}" if r.error else "")
    logger.info("%d/%d steps passed", passed, total)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

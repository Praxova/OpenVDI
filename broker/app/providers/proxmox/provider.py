"""Proxmox VE implementation of HypervisorProvider.

Milestone 1 surface: capabilities, ping, close, list_nodes,
get_node_status, list_storage, VM lifecycle (clone/start/stop/shutdown/
destroy/status/list), guest agent (ping, get_users), console tickets
(noVNC; SPICE stubbed), and task tracking. Snapshots and the remaining
guest agent methods arrive in later prompts.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import ClassVar
from urllib.parse import quote as _urlquote

from app.providers import register_provider
from app.providers.base import (
    CloneRequest,
    ConsoleKind,
    ConsoleTicket,
    GuestUser,
    NodeInfo,
    NodeStatus,
    NoVNCTicket,
    PowerState,
    ProviderCapabilities,
    SpiceTicket,
    StorageInfo,
    TaskHandle,
    TaskStatus,
    VMRef,
    VMStatus,
)
from app.providers.exceptions import (
    ProviderAuthError,
    ProviderCapabilityError,
    ProviderError,
    ProviderLockError,
    ProviderTaskError,
    ProviderTimeoutError,
)

from .client import _ProxmoxClient
from .types import (
    make_task_handle,
    make_vm_ref,
    unpack_task_handle,
    unpack_vm_ref,
)

logger = logging.getLogger(__name__)


@register_provider
class ProxmoxProvider:
    """Proxmox VE implementation of HypervisorProvider."""

    provider_type: ClassVar[str] = "proxmox"

    def __init__(
        self,
        api_url: str,
        token_id: str,
        token_secret: str,
        verify_ssl: bool = True,
    ) -> None:
        self._client = _ProxmoxClient(
            api_url=api_url,
            token_id=token_id,
            token_secret=token_secret,
            verify_ssl=verify_ssl,
        )
        self._capabilities: ProviderCapabilities | None = None

    async def __aenter__(self) -> ProxmoxProvider:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ── Capabilities & lifecycle ──────────────────────────────

    async def capabilities(self) -> ProviderCapabilities:
        if self._capabilities is None:
            self._capabilities = ProviderCapabilities(
                provider_type="proxmox",
                linked_clones=True,
                full_clones=True,
                snapshots=True,
                guest_agent=True,
                live_migration=True,
                console_kinds=frozenset(
                    {ConsoleKind.NOVNC, ConsoleKind.SPICE}
                ),
                supports_pool_tags=True,
                supports_resource_pools=True,
            )
        return self._capabilities

    async def ping(self) -> bool:
        """Quick connectivity check. Returns True on success, False on
        transient failure. Auth failures still raise ProviderAuthError
        because a misconfigured token is not a 'down cluster'.
        """
        try:
            await self._client._request("GET", "/version")
            return True
        except ProviderAuthError:
            raise
        except ProviderError:
            return False

    # ── Cluster & placement ───────────────────────────────────

    async def list_nodes(self) -> list[NodeInfo]:
        raw = await self._client._request("GET", "/nodes")
        nodes = raw if isinstance(raw, list) else []
        return [_node_info_from_dict(d) for d in nodes]

    async def get_node_status(self, node: str) -> NodeStatus:
        raw = await self._client._request("GET", f"/nodes/{node}/status")
        if not isinstance(raw, dict):
            raw = {}
        return _node_status_from_dict(node, raw)

    async def list_storage(self, node: str) -> list[StorageInfo]:
        raw = await self._client._request("GET", f"/nodes/{node}/storage")
        stores = raw if isinstance(raw, list) else []
        return [_storage_info_from_dict(d) for d in stores]

    # ── VM lifecycle ──────────────────────────────────────────

    async def clone_vm(self, req: CloneRequest) -> TaskHandle:
        """Clone a Proxmox template into a new VM.

        Linked-clone-from-template is the only mode — Proxmox's default
        when the source is a template. `full` and `snapname` are never
        sent (see docs/architecture.md → Cloning Model).
        """
        src_node, src_vmid = unpack_vm_ref(req.source_ref)

        opts = req.provider_opts or {}
        newid = opts.get("newid")
        if newid is None:
            raise ProviderCapabilityError(
                "Proxmox requires a destination VMID via "
                "CloneRequest.provider_opts['newid']",
                provider_type="proxmox",
            )

        body = {
            "newid": int(newid),
            "name": req.new_name,
            "storage": req.target_storage,
            "pool": req.target_pool,
            "target_node": req.target_node,
            "description": req.description,
        }
        upid = await self._client._request(
            "POST",
            f"/nodes/{src_node}/qemu/{src_vmid}/clone",
            data=body,
        )
        target_node = req.target_node or src_node
        return make_task_handle(target_node, upid)

    async def start_vm(self, ref: VMRef) -> TaskHandle:
        node, vmid = unpack_vm_ref(ref)
        upid = await self._client._request(
            "POST", f"/nodes/{node}/qemu/{vmid}/status/start"
        )
        return make_task_handle(node, upid)

    async def stop_vm(self, ref: VMRef) -> TaskHandle:
        """Forceful stop — equivalent to pulling the power cord."""
        node, vmid = unpack_vm_ref(ref)
        upid = await self._client._request(
            "POST", f"/nodes/{node}/qemu/{vmid}/status/stop"
        )
        return make_task_handle(node, upid)

    async def shutdown_vm(
        self,
        ref: VMRef,
        timeout_seconds: int = 120,
        force: bool = False,
    ) -> TaskHandle:
        """Graceful ACPI shutdown. If `force` is True, Proxmox escalates
        to a hard stop after `timeout_seconds` without a guest response.
        """
        node, vmid = unpack_vm_ref(ref)
        body = {
            "timeout": timeout_seconds,
            "force_stop": 1 if force else 0,
        }
        upid = await self._client._request(
            "POST",
            f"/nodes/{node}/qemu/{vmid}/status/shutdown",
            data=body,
        )
        return make_task_handle(node, upid)

    async def destroy_vm(self, ref: VMRef, purge: bool = True) -> TaskHandle:
        """Delete the VM and its owned storage. VM must be stopped.

        Retries up to 3 times on ProviderLockError with exponential
        backoff (5s, 10s, 20s). The client-layer retry already handles
        transient 5xx; this provider-layer retry covers slower LVM-thin
        lock contention that outlasts the client's ~6s budget.
        """
        node, vmid = unpack_vm_ref(ref)
        params = {"purge": 1 if purge else 0}
        backoff = 5.0
        last: ProviderLockError | None = None
        for attempt in range(3):
            try:
                upid = await self._client._request(
                    "DELETE", f"/nodes/{node}/qemu/{vmid}", params=params,
                )
                return make_task_handle(node, upid)
            except ProviderLockError as exc:
                last = exc
                if attempt < 2:
                    sleep_s = backoff * (2 ** attempt)
                    logger.warning(
                        "destroy_vm lock contention; retrying",
                        extra={
                            "node": node, "vmid": vmid,
                            "attempt": attempt + 1, "sleep_s": sleep_s,
                        },
                    )
                    await asyncio.sleep(sleep_s)
        assert last is not None  # loop always assigns on failure
        raise last

    async def get_vm_status(self, ref: VMRef) -> VMStatus:
        node, vmid = unpack_vm_ref(ref)
        raw = await self._client._request(
            "GET", f"/nodes/{node}/qemu/{vmid}/status/current",
        )
        if not isinstance(raw, dict):
            raw = {}
        return _vm_status_from_dict(ref, raw)

    async def list_vms(self, node: str | None = None) -> list[VMStatus]:
        """List VMs on a node. When `node` is None, aggregate across
        every online node from list_nodes(). A failing node is logged
        and skipped, not fatal."""
        if node is not None:
            return await self._list_vms_on_node(node)

        results: list[VMStatus] = []
        for n in await self.list_nodes():
            if n.status != "online":
                continue
            try:
                results.extend(await self._list_vms_on_node(n.node))
            except ProviderError as exc:
                logger.warning(
                    "list_vms: skipping node due to error",
                    extra={"node": n.node, "error": str(exc)},
                )
        return results

    async def _list_vms_on_node(self, node: str) -> list[VMStatus]:
        raw = await self._client._request(
            "GET", f"/nodes/{node}/qemu", params=dict(full=1),
        )
        items = raw if isinstance(raw, list) else []
        out: list[VMStatus] = []
        for d in items:
            if not isinstance(d, dict):
                continue
            ref = make_vm_ref(node, int(d.get("vmid", 0)))
            out.append(_vm_status_from_dict(ref, d))
        return out

    # ── Guest agent ───────────────────────────────────────────

    async def agent_ping(self, ref: VMRef) -> bool:
        """Return True if the guest agent responds, False otherwise.

        Does NOT raise on 'agent unreachable' — that's the common case
        during VM boot. Auth errors DO propagate (misconfig isn't a
        'no agent' signal).

        Uses max_retries=1: agent-unreachable manifests as a 500 that
        the client would otherwise retry 3x (~6s of wasted latency).
        We know False is a valid outcome here, so we don't retry.
        """
        node, vmid = unpack_vm_ref(ref)
        try:
            await self._client._request(
                "POST",
                f"/nodes/{node}/qemu/{vmid}/agent/ping",
                max_retries=1,
            )
            return True
        except ProviderAuthError:
            raise
        except ProviderError:
            return False

    async def agent_get_users(self, ref: VMRef) -> list[GuestUser]:
        """List OS-level logged-in users as reported by the guest agent.

        Raises ProviderError subclasses on agent unreachable. Callers
        should agent_ping first if they need a liveness gate.
        """
        node, vmid = unpack_vm_ref(ref)
        resp = await self._client._request(
            "GET", f"/nodes/{node}/qemu/{vmid}/agent/get-users",
        )
        # Proxmox wraps agent payloads: outer "data" is unwrapped by
        # _request, leaving {"result": [...]} here.
        users_raw = (
            resp.get("result", []) if isinstance(resp, dict) else []
        )
        out: list[GuestUser] = []
        for u in users_raw:
            if not isinstance(u, dict):
                continue
            login_time = u.get("login-time")
            out.append(
                GuestUser(
                    username=u.get("user", ""),
                    login_time=int(login_time) if login_time is not None else None,
                    domain=u.get("domain"),
                )
            )
        return out

    # ── Console tickets ───────────────────────────────────────

    async def get_console_ticket(
        self, ref: VMRef, kind: ConsoleKind,
    ) -> ConsoleTicket:
        """Issue a console ticket for the given kind.

        Raises ProviderCapabilityError if kind is not supported.
        """
        if kind == ConsoleKind.NOVNC:
            return await self._get_novnc_ticket(ref)
        if kind == ConsoleKind.SPICE:
            return await self._get_spice_ticket(ref)
        raise ProviderCapabilityError(
            f"console kind {kind.value!r} is not supported by the Proxmox provider",
            provider_type="proxmox",
        )

    async def _get_novnc_ticket(self, ref: VMRef) -> NoVNCTicket:
        """Request a websocket VNC ticket and build the browser URL."""
        node, vmid = unpack_vm_ref(ref)
        # PARAM_MAP in params.py converts generate_password to the
        # kebab-case form Proxmox expects.
        body = {"websocket": 1, "generate_password": 1}
        resp = await self._client._request(
            "POST", f"/nodes/{node}/qemu/{vmid}/vncproxy", data=body,
        )
        if not isinstance(resp, dict):
            raise ProviderError(
                f"vncproxy returned non-dict: {type(resp).__name__}",
                provider_type="proxmox",
            )

        port = int(resp["port"])
        ticket = resp["ticket"]
        password = resp.get("password", "") or ""
        cert = resp.get("cert")

        # PVE 9.x wss:// form. The 'port' query param duplicates the URL
        # port — that's per spec.
        websocket_url = (
            f"wss://{node}:{port}/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket"
            f"?port={port}&vncticket={_urlquote(ticket, safe='')}"
        )

        return NoVNCTicket(
            websocket_url=websocket_url,
            password=password,
            cert_pem=cert,
        )

    async def _get_spice_ticket(self, ref: VMRef) -> SpiceTicket:
        """Stubbed for M1. The M1 test exercises noVNC only."""
        raise ProviderCapabilityError(
            "SPICE ticket retrieval is stubbed for M1; "
            "see docs/providers/proxmox.md",
            provider_type="proxmox",
        )

    # ── Task tracking ─────────────────────────────────────────

    async def get_task_status(self, handle: TaskHandle) -> TaskStatus:
        node, upid = unpack_task_handle(handle)
        raw = await self._client.get_task_status_raw(node, upid)

        status = raw.get("status")
        if status == "running":
            return TaskStatus(
                state="running",
                success=None,
                error_message=None,
                raw=raw,
            )

        # stopped
        exitstatus = raw.get("exitstatus")
        if exitstatus is None:
            # Defensive — shouldn't happen on a stopped task per the spec
            # quirk, but the API occasionally lies.
            return TaskStatus(
                state="stopped",
                success=False,
                error_message="task stopped but exitstatus missing from response",
                raw=raw,
            )
        if exitstatus == "OK":
            return TaskStatus(
                state="stopped",
                success=True,
                error_message=None,
                raw=raw,
            )
        return TaskStatus(
            state="stopped",
            success=False,
            error_message=str(exitstatus),
            raw=raw,
        )

    async def wait_for_task(
        self,
        handle: TaskHandle,
        timeout_seconds: int = 600,
        poll_interval: float = 1.0,
    ) -> TaskStatus:
        """Poll get_task_status until stopped or timeout.

        Default timeout of 600s is tuned for clone operations on
        LVM-thin. Callers MUST override for faster ops (start/stop: 30s)
        or longer ones (full clones on slow storage: 1800s+).

        Raises ProviderTimeoutError on timeout.
        Raises ProviderTaskError if the task completed with success=False.
        """
        start = time.monotonic()
        while True:
            status = await self.get_task_status(handle)
            if status.state == "stopped":
                if status.success:
                    return status
                raise ProviderTaskError(
                    status.error_message or "task failed",
                    provider_type="proxmox",
                    detail={"handle": handle.data, "raw": status.raw},
                )
            elapsed = time.monotonic() - start
            if elapsed >= timeout_seconds:
                raise ProviderTimeoutError(
                    f"task did not complete within {timeout_seconds}s",
                    provider_type="proxmox",
                    detail={"handle": handle.data, "elapsed": elapsed},
                )
            await asyncio.sleep(poll_interval)


# ── Response mappers ──────────────────────────────────────────

def _node_info_from_dict(d: dict) -> NodeInfo:
    name = d.get("node", "")
    raw_status = d.get("status", "")
    # M1: 'online' is online; anything else treated as 'offline'.
    status = "online" if raw_status == "online" else "offline"
    return NodeInfo(
        node=name,
        display_name=name,
        status=status,
        cpu_cores=int(d.get("maxcpu", 0) or 0),
        memory_bytes=int(d.get("maxmem", 0) or 0),
    )


def _node_status_from_dict(node: str, d: dict) -> NodeStatus:
    cpu = d.get("cpu", 0.0) or 0.0
    memory = d.get("memory") or {}
    return NodeStatus(
        node=node,
        cpu_usage_percent=float(cpu) * 100.0,
        memory_used_bytes=int(memory.get("used", 0) or 0),
        memory_total_bytes=int(memory.get("total", 0) or 0),
        uptime_seconds=int(d.get("uptime", 0) or 0),
        kernel_version=d.get("kversion"),
    )


def _storage_info_from_dict(d: dict) -> StorageInfo:
    content_raw = d.get("content", "") or ""
    content_types = frozenset(
        piece.strip()
        for piece in content_raw.split(",")
        if piece.strip()
    )
    return StorageInfo(
        name=d.get("storage", ""),
        storage_type=d.get("type", ""),
        shared=bool(int(d.get("shared", 0) or 0)),
        total_bytes=int(d.get("total", 0) or 0),
        used_bytes=int(d.get("used", 0) or 0),
        content_types=content_types,
    )


def _normalize_power_state(s: str | None) -> PowerState:
    if s in ("running", "stopped", "paused"):
        return s
    return "unknown"


def _vm_status_from_dict(ref: VMRef, d: dict) -> VMStatus:
    # Proxmox returns tags as a semicolon-separated string; may be absent.
    tag_str = d.get("tags", "") or ""
    tags = frozenset(t for t in (x.strip() for x in tag_str.split(";")) if t)
    return VMStatus(
        ref=ref,
        name=d.get("name", ""),
        power_state=_normalize_power_state(d.get("status")),
        cpu_cores=int(d.get("cpus", 0) or 0),
        memory_bytes=int(d.get("maxmem", 0) or 0),
        disk_bytes=int(d.get("maxdisk", 0) or 0),
        uptime_seconds=int(d.get("uptime", 0) or 0),
        is_template=bool(d.get("template", 0)),
        guest_agent_configured=bool(d.get("agent", 0)),
        lock=d.get("lock"),
        tags=tags,
        raw=d,
    )

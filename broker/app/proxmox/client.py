"""Async Proxmox VE API client for VDI operations.

Uses httpx with connection pooling. One instance per cluster.
Auth via PVE API tokens (stateless, no CSRF needed).
"""

import asyncio
import logging

import httpx

from broker.app.proxmox.exceptions import (
    ProxmoxAuthError,
    ProxmoxError,
    ProxmoxNotFoundError,
    ProxmoxTaskError,
    ProxmoxTimeoutError,
)

logger = logging.getLogger(__name__)

_ERROR_MAP = {
    401: ProxmoxAuthError,
    403: ProxmoxAuthError,
    404: ProxmoxNotFoundError,
}


class ProxmoxClient:
    """Async wrapper for VDI-relevant Proxmox API operations.

    One instance per registered cluster. Manages httpx.AsyncClient
    with connection pooling and retry logic.
    """

    def __init__(
        self,
        api_url: str,
        token_id: str,
        token_secret: str,
        verify_ssl: bool = False,
    ):
        self.api_url = api_url.rstrip("/")
        self._auth_header = f"PVEAPIToken={token_id}={token_secret}"
        self._client = httpx.AsyncClient(
            base_url=f"{self.api_url}/api2/json",
            headers={"Authorization": self._auth_header},
            verify=verify_ssl,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ── HTTP helpers ──────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict | None = None,
        params: dict | None = None,
        timeout: float | None = None,
        retries: int = 3,
    ) -> dict:
        """Issue a request with retry on 5xx errors."""
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                kwargs: dict = {}
                if data is not None:
                    kwargs["data"] = {k: v for k, v in data.items() if v is not None}
                if params is not None:
                    kwargs["params"] = {k: v for k, v in params.items() if v is not None}
                if timeout is not None:
                    kwargs["timeout"] = timeout

                resp = await self._client.request(method, path, **kwargs)
                return self._handle_response(resp, path)

            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning("Proxmox %s %s attempt %d failed: %s, retrying in %ds", method, path, attempt + 1, exc, wait)
                    await asyncio.sleep(wait)
                continue

            except ProxmoxError:
                raise

        raise ProxmoxTimeoutError(
            f"Failed after {retries} attempts: {last_exc}",
            endpoint=path,
        )

    def _handle_response(self, resp: httpx.Response, path: str) -> dict:
        """Extract data from Proxmox API response envelope."""
        if resp.status_code >= 400:
            body = resp.text
            exc_cls = _ERROR_MAP.get(resp.status_code, ProxmoxError)
            raise exc_cls(
                status_code=resp.status_code,
                message=body,
                endpoint=path,
            )
        envelope = resp.json()
        return envelope.get("data", envelope)

    # ── VM Lifecycle ──────────────────────────────────────

    async def clone_vm(
        self,
        node: str,
        template_vmid: int,
        new_vmid: int,
        name: str,
        *,
        storage: str | None = None,
        full: bool = False,
        pool: str | None = None,
        target_node: str | None = None,
        description: str | None = None,
        snapname: str | None = None,
    ) -> str:
        """Clone a VM/template. Returns UPID of the async clone task.

        POST /nodes/{node}/qemu/{vmid}/clone
        """
        data = {
            "newid": new_vmid,
            "name": name,
            "full": int(full),
            "storage": storage,
            "pool": pool,
            "target": target_node,
            "description": description,
            "snapname": snapname,
        }
        result = await self._request(
            "POST",
            f"/nodes/{node}/qemu/{template_vmid}/clone",
            data=data,
            timeout=300.0,
        )
        upid = result if isinstance(result, str) else str(result)
        logger.info("Clone task started: template=%d -> vmid=%d upid=%s", template_vmid, new_vmid, upid)
        return upid

    async def start_vm(self, node: str, vmid: int) -> str:
        """Start a VM. Returns UPID.

        POST /nodes/{node}/qemu/{vmid}/status/start
        """
        result = await self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/start")
        return result if isinstance(result, str) else str(result)

    async def stop_vm(self, node: str, vmid: int) -> str:
        """Immediate stop (power-off). Returns UPID.

        POST /nodes/{node}/qemu/{vmid}/status/stop
        """
        result = await self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/stop")
        return result if isinstance(result, str) else str(result)

    async def destroy_vm(
        self,
        node: str,
        vmid: int,
        *,
        purge: bool = True,
        destroy_unreferenced_disks: bool = True,
        max_attempts: int = 3,
        lock_retry_delay: float = 15.0,
    ) -> str:
        """Destroy a VM and its disks. Must be stopped first. Returns UPID.

        Retries on lock contention (e.g. clone still holding LVM lock).
        DELETE /nodes/{node}/qemu/{vmid}
        """
        params = {
            "purge": int(purge),
            "destroy-unreferenced-disks": int(destroy_unreferenced_disks),
        }
        for attempt in range(max_attempts):
            try:
                result = await self._request(
                    "DELETE",
                    f"/nodes/{node}/qemu/{vmid}",
                    params=params,
                )
                upid = result if isinstance(result, str) else str(result)
                return upid
            except (ProxmoxError, ProxmoxTaskError) as exc:
                if "lock" in str(exc).lower() and attempt < max_attempts - 1:
                    logger.warning(
                        "VM %d lock held, retrying in %ds (attempt %d/%d)",
                        vmid, lock_retry_delay, attempt + 1, max_attempts,
                    )
                    await asyncio.sleep(lock_retry_delay)
                else:
                    raise

    async def get_vm_status(self, node: str, vmid: int) -> dict:
        """Get current VM status including power state, resources, agent info.

        GET /nodes/{node}/qemu/{vmid}/status/current
        """
        return await self._request("GET", f"/nodes/{node}/qemu/{vmid}/status/current")

    async def list_vms(self, node: str, *, full: bool = True) -> list[dict]:
        """List all VMs on a node.

        GET /nodes/{node}/qemu
        """
        params = {"full": int(full)} if full else None
        result = await self._request("GET", f"/nodes/{node}/qemu", params=params)
        return result if isinstance(result, list) else []

    # ── Connection ────────────────────────────────────────

    async def get_vnc_ticket(
        self,
        node: str,
        vmid: int,
        *,
        websocket: bool = True,
        generate_password: bool = True,
    ) -> dict:
        """Get a VNC proxy ticket for noVNC/websockify.

        POST /nodes/{node}/qemu/{vmid}/vncproxy

        Returns: {port, ticket, cert, upid, user, password?}
        """
        data = {
            "websocket": int(websocket),
            "generate-password": int(generate_password),
        }
        return await self._request(
            "POST",
            f"/nodes/{node}/qemu/{vmid}/vncproxy",
            data=data,
        )

    # ── Guest Agent ───────────────────────────────────────

    async def agent_ping(self, node: str, vmid: int) -> bool:
        """Ping the QEMU guest agent. Returns True if responsive.

        POST /nodes/{node}/qemu/{vmid}/agent/ping
        """
        try:
            await self._request(
                "POST",
                f"/nodes/{node}/qemu/{vmid}/agent/ping",
                timeout=10.0,
                retries=1,
            )
            return True
        except (ProxmoxError, httpx.TimeoutException):
            return False

    async def agent_get_users(self, node: str, vmid: int) -> list[dict]:
        """Get logged-in OS users via guest agent.

        GET /nodes/{node}/qemu/{vmid}/agent/get-users

        Returns list of user dicts with login timestamps.
        """
        result = await self._request(
            "GET",
            f"/nodes/{node}/qemu/{vmid}/agent/get-users",
            timeout=10.0,
            retries=1,
        )
        # Proxmox wraps agent results in {"result": [...]}
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return result if isinstance(result, list) else []

    # ── Task Tracking ─────────────────────────────────────

    async def get_task_status(self, node: str, upid: str) -> dict:
        """Check status of an async Proxmox task.

        GET /nodes/{node}/tasks/{upid}/status

        Returns: {status: "running"|"stopped", exitstatus?: "OK"|...}
        """
        return await self._request("GET", f"/nodes/{node}/tasks/{upid}/status")

    async def wait_for_task(
        self,
        node: str,
        upid: str,
        *,
        timeout: int = 120,
        poll_interval: float = 1.0,
    ) -> dict:
        """Poll a task until it completes or times out.

        Raises ProxmoxTaskError if task exits with non-OK status.
        Raises ProxmoxTimeoutError if task doesn't finish in time.
        """
        elapsed = 0.0
        while elapsed < timeout:
            status = await self.get_task_status(node, upid)
            if status.get("status") == "stopped":
                exit_status = status.get("exitstatus", "")
                if exit_status != "OK":
                    raise ProxmoxTaskError(upid=upid, exit_status=exit_status)
                logger.info("Task completed OK: %s", upid)
                return status
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise ProxmoxTimeoutError(
            f"Task {upid} did not complete within {timeout}s",
            endpoint=f"/nodes/{node}/tasks/{upid}/status",
        )

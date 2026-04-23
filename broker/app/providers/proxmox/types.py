"""VMRef / TaskHandle encode/decode for the Proxmox provider.

The broker treats VMRef.data and TaskHandle.data as opaque. The Proxmox
provider packs (node, vmid) into VMRef.data and (node, upid) into
TaskHandle.data. These helpers centralize the packing so if we ever need
to change the shape, one file changes.
"""
from __future__ import annotations

from typing import TypedDict

from app.providers.base import TaskHandle, VMRef

PROVIDER_TYPE = "proxmox"


class _VMRefData(TypedDict):
    node: str
    vmid: int


class _TaskHandleData(TypedDict):
    node: str
    upid: str


def make_vm_ref(node: str, vmid: int) -> VMRef:
    return VMRef(
        provider_type=PROVIDER_TYPE,
        data={"node": node, "vmid": vmid},
    )


def unpack_vm_ref(ref: VMRef) -> tuple[str, int]:
    """Return (node, vmid). Validates provider_type and shape.

    Raises ValueError if the ref isn't from this provider or is malformed.
    """
    if ref.provider_type != PROVIDER_TYPE:
        raise ValueError(
            f"expected proxmox VMRef, got {ref.provider_type!r}"
        )
    data = ref.data
    if not isinstance(data, dict) or "node" not in data or "vmid" not in data:
        raise ValueError(f"malformed Proxmox VMRef data: {data!r}")
    return data["node"], int(data["vmid"])


def make_task_handle(node: str, upid: str) -> TaskHandle:
    return TaskHandle(
        provider_type=PROVIDER_TYPE,
        data={"node": node, "upid": upid},
    )


def unpack_task_handle(handle: TaskHandle) -> tuple[str, str]:
    """Return (node, upid). Validates provider_type and shape."""
    if handle.provider_type != PROVIDER_TYPE:
        raise ValueError(
            f"expected proxmox TaskHandle, got {handle.provider_type!r}"
        )
    data = handle.data
    if not isinstance(data, dict) or "node" not in data or "upid" not in data:
        raise ValueError(f"malformed Proxmox TaskHandle data: {data!r}")
    return data["node"], data["upid"]

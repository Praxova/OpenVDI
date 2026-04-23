"""Proxmox-local exceptions. All extend ProviderError — the broker-level
catch surface stays unified."""
from __future__ import annotations

from app.providers.exceptions import ProviderError


class ProxmoxError(ProviderError):
    """Proxmox-specific error that doesn't map to one of the base categories.

    Prefer raising the base types (ProviderAuthError, ProviderNotFoundError,
    ProviderLockError, etc.) from the public surface. Use ProxmoxError only
    for internal client signaling where nothing else fits.
    """

    def __init__(self, status_code: int, message: str, endpoint: str):
        super().__init__(
            message,
            provider_type="proxmox",
            detail={"status_code": status_code, "endpoint": endpoint},
        )
        self.status_code = status_code
        self.endpoint = endpoint

    def __str__(self) -> str:
        return (
            f"[proxmox] HTTP {self.status_code} {self.endpoint}: {self.message}"
        )

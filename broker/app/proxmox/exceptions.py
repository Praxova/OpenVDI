"""Proxmox API error types."""


class ProxmoxError(Exception):
    """Base exception for Proxmox API errors."""

    def __init__(self, status_code: int, message: str, endpoint: str):
        self.status_code = status_code
        self.message = message
        self.endpoint = endpoint
        super().__init__(f"[{status_code}] {endpoint}: {message}")


class ProxmoxAuthError(ProxmoxError):
    """401/403 from Proxmox -- bad token or insufficient permissions."""


class ProxmoxNotFoundError(ProxmoxError):
    """404 -- VM, node, or resource doesn't exist."""


class ProxmoxTimeoutError(ProxmoxError):
    """Request or task timed out."""

    def __init__(self, message: str, endpoint: str = ""):
        self.status_code = 0
        self.message = message
        self.endpoint = endpoint
        Exception.__init__(self, f"Timeout {endpoint}: {message}")


class ProxmoxTaskError(ProxmoxError):
    """Async task completed with non-OK exit status."""

    def __init__(self, upid: str, exit_status: str, endpoint: str = ""):
        self.upid = upid
        self.exit_status = exit_status
        self.status_code = 0
        self.message = f"Task {upid} failed: {exit_status}"
        self.endpoint = endpoint
        Exception.__init__(self, self.message)

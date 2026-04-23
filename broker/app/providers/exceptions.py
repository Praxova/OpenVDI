"""Provider-layer exception hierarchy.

This is the public surface caught by the broker. Concrete providers may
define their own subclasses (e.g. ProxmoxClientError) for internal use,
but the types the broker catches are always one of these.
"""
from __future__ import annotations


class ProviderError(Exception):
    """Base class for all provider-layer exceptions."""

    def __init__(
        self,
        message: str,
        provider_type: str,
        detail: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.provider_type = provider_type
        self.detail = detail or {}

    def __str__(self) -> str:
        return f"[{self.provider_type}] {self.message}"


class ProviderAuthError(ProviderError):
    """Authentication or authorization failure."""


class ProviderNotFoundError(ProviderError):
    """Target resource (VM, node, template) does not exist."""


class ProviderTimeoutError(ProviderError):
    """Request or task did not complete in the allowed time."""


class ProviderTaskError(ProviderError):
    """Async task completed with failure. The `detail` dict includes
    the provider's raw error response."""


class ProviderLockError(ProviderError):
    """Target resource is locked by another operation. Typically
    transient; providers SHOULD have exhausted their internal retries
    before raising this."""


class ProviderCapabilityError(ProviderError):
    """Requested operation is not supported by this provider."""

"""Provider registry. Concrete providers register themselves here."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import HypervisorProvider

_registry: dict[str, type["HypervisorProvider"]] = {}


def register_provider(
    cls: type["HypervisorProvider"],
) -> type["HypervisorProvider"]:
    """Decorator. Registers cls under cls.provider_type."""
    provider_type = getattr(cls, "provider_type", None)
    if not provider_type:
        raise TypeError(f"{cls.__name__} is missing a provider_type ClassVar")
    if provider_type in _registry:
        raise ValueError(f"Provider type {provider_type!r} already registered")
    _registry[provider_type] = cls
    return cls


def get_provider_class(provider_type: str) -> type["HypervisorProvider"]:
    """Look up a registered provider class by type string."""
    try:
        return _registry[provider_type]
    except KeyError:
        known = ", ".join(sorted(_registry)) or "<none>"
        raise ValueError(
            f"Unknown provider type: {provider_type!r}. Known: {known}"
        )


def list_provider_types() -> list[str]:
    """Sorted list of registered provider types (for admin UI)."""
    return sorted(_registry)


# Auto-register concrete providers. Imports must live at the bottom so
# register_provider and friends are defined before the side-effect import
# runs.
from . import proxmox  # noqa: E402, F401

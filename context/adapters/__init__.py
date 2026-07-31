"""Application adapters.

`adapter_for()` returns the first adapter that claims a resource, falling back to
the generic desktop-entry launcher.
"""

from __future__ import annotations

from ..resources import Resource
from .base import Adapter, GenericAdapter, launch_desktop_entry
from .firefox import FirefoxAdapter
from .vscode import VSCodeAdapter

SPECIFIC: list = [FirefoxAdapter(), VSCodeAdapter()]
GENERIC = GenericAdapter()


def adapter_for(resource: Resource) -> Adapter:
    for adapter in SPECIFIC:
        if adapter.handles(resource):
            return adapter
    return GENERIC


def describe(resource: Resource) -> str:
    return adapter_for(resource).describe(resource)


def configurable(resource: Resource) -> bool:
    """Whether this resource has options worth showing a config page for."""
    return adapter_for(resource) is not GENERIC


def supports_paths(resource: Resource) -> bool:
    """Whether this resource opens a folder, file or workspace path."""
    return isinstance(adapter_for(resource), VSCodeAdapter)


def supports_profiles(resource: Resource) -> bool:
    """Whether this resource can choose between a dedicated and the main profile."""
    return isinstance(adapter_for(resource), FirefoxAdapter)


__all__ = [
    "Adapter",
    "FirefoxAdapter",
    "VSCodeAdapter",
    "GenericAdapter",
    "adapter_for",
    "configurable",
    "describe",
    "launch_desktop_entry",
    "supports_paths",
    "supports_profiles",
]

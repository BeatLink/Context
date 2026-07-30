"""Application adapters.

`adapter_for()` returns the first adapter that claims a resource, falling back to
the generic desktop-entry launcher.
"""

from __future__ import annotations

from ..resources import Resource
from .base import Adapter, GenericAdapter, launch_desktop_entry
from .firefox import FirefoxAdapter

SPECIFIC: list = [FirefoxAdapter()]
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


__all__ = [
    "Adapter",
    "FirefoxAdapter",
    "GenericAdapter",
    "adapter_for",
    "configurable",
    "describe",
    "launch_desktop_entry",
]

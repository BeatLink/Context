"""Application adapters.

`adapter_for()` returns the first adapter that claims a resource, falling back to
the generic desktop-entry launcher.
"""

from __future__ import annotations

from context.state.resources import Resource
from .base import Adapter, GenericAdapter, isolating, launch_desktop_entry
from .firefox import FirefoxAdapter
from .terminal import TerminalAdapter
from .vscode import VSCodeAdapter

SPECIFIC: list = [FirefoxAdapter(), VSCodeAdapter(), TerminalAdapter()]
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
    return isinstance(adapter_for(resource), (VSCodeAdapter, TerminalAdapter))


def supports_command(resource: Resource) -> bool:
    """Whether this resource can be given a command to run."""
    return isinstance(adapter_for(resource), TerminalAdapter)


def supports_profiles(resource: Resource) -> bool:
    """Whether this resource can choose between a dedicated and the main profile."""
    return isinstance(adapter_for(resource), FirefoxAdapter)


__all__ = [
    "isolating",
    "Adapter",
    "FirefoxAdapter",
    "TerminalAdapter",
    "VSCodeAdapter",
    "GenericAdapter",
    "adapter_for",
    "configurable",
    "describe",
    "launch_desktop_entry",
    "supports_command",
    "supports_paths",
    "supports_profiles",
]

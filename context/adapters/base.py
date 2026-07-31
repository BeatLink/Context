"""The application adapter interface.

An adapter knows how to open one application *at* something — a set of URLs, a
folder, a workspace file. Adapters are matched to a resource by desktop-entry id.

`GenericAdapter` is the fallback: launch the desktop entry as-is. It handles every
app with no special integration, so adding an adapter is always additive.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import os

from gi.repository import Gio

from ..resources import Resource


# Variables that belong to whatever started the launcher and must never reach
# an application it launches.
#
# ELECTRON_RUN_AS_NODE makes an Electron binary run as plain Node, so it never
# builds a window: it dies with "Cannot find module 'electron'". Editors set it
# for their own integrated terminals, so a Context started from one inherits it
# and every Electron application in every context fails — reported as "I can't
# launch Trilium", with the launch itself reporting success.
STRIPPED_VARS = (
    "ELECTRON_RUN_AS_NODE",
    "ELECTRON_NO_ATTACH_CONSOLE",
    "CONTEXT_LAYER_SHELL_PRELOADED",
)


def child_env() -> dict[str, str]:
    """The environment launched apps should get.

    The launcher may be running with gtk4-layer-shell in LD_PRELOAD so it can
    dock itself. Inheriting that injects the library into every app it starts,
    which segfaults Firefox — nothing launched should see it.
    """
    env = dict(os.environ)
    preload = env.get("LD_PRELOAD")
    if preload:
        kept = [p for p in preload.split(":") if p and "gtk4-layer-shell" not in p]
        if kept:
            env["LD_PRELOAD"] = ":".join(kept)
        else:
            env.pop("LD_PRELOAD", None)
    for name in STRIPPED_VARS:
        env.pop(name, None)
    return env


def launch_desktop_entry(app_id: str, uris: list[str] | None = None) -> None:
    try:
        info = Gio.DesktopAppInfo.new(app_id)
    except TypeError as exc:
        raise LookupError(f"no desktop entry for {app_id}") from exc
    if info is None:
        raise LookupError(f"no desktop entry for {app_id}")

    context = Gio.AppLaunchContext()
    # Gio copies the launcher's own environment rather than taking a dict, so
    # anything removed has to be unset explicitly — setting the rest is not
    # enough to drop a variable that should not be there.
    wanted = child_env()
    for key, value in wanted.items():
        context.setenv(key, value)
    for key in set(os.environ) - set(wanted):
        context.unsetenv(key)

    if uris:
        info.launch_uris(uris, context)
    else:
        info.launch([], context)


@runtime_checkable
class Adapter(Protocol):
    name: str

    def handles(self, resource: Resource) -> bool:
        """Whether this adapter should launch `resource`."""

    def launch(self, resource: Resource, context_id: str) -> None:
        """Open the resource. Raise on failure; the launcher collects errors.

        `context_id` scopes any per-context state the adapter keeps, such as a
        browser profile directory.
        """

    def describe(self, resource: Resource) -> str:
        """Short human summary of what this resource opens, for the UI."""

    def teardown(self, resource: Resource, context_id: str) -> None:
        """Discard per-context state. Only called for ephemeral contexts."""


class GenericAdapter:
    name = "generic"

    def handles(self, resource: Resource) -> bool:
        return True

    def launch(self, resource: Resource, context_id: str) -> None:
        # Hand any URLs to the app's own handler; most apps accept them.
        launch_desktop_entry(resource.app_id, resource.urls or None)

    def describe(self, resource: Resource) -> str:
        if resource.urls:
            return f"{len(resource.urls)} URL{'s' if len(resource.urls) != 1 else ''}"
        if resource.path:
            return resource.path
        return ""

    def teardown(self, resource: Resource, context_id: str) -> None:
        return None

"""The application adapter interface.

An adapter knows how to open one application *at* something — a set of URLs, a
folder, a workspace file. Adapters are matched to a resource by desktop-entry id.

`GenericAdapter` is the fallback: launch the desktop entry as-is. It handles every
app with no special integration, so adding an adapter is always additive.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import shlex
import subprocess
from typing import Protocol, runtime_checkable

from gi.repository import Gio

from context.system import isolation
from context.state.resources import Resource


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


# Whether the launch in progress is isolated, and which context it belongs to.
#
# A context variable rather than an argument: every adapter would otherwise need
# the flag threaded through `launch()` and on to `child_env()`, and adapters are
# the extension point most likely to be written by someone who does not know
# isolation exists. A plain module global would be wrong — launches run on
# worker threads, so two contexts can be starting at once.
_isolated_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "context_isolated", default=None
)


@contextlib.contextmanager
def isolating(context_id: str | None):
    """Mark everything launched inside this block as isolated."""
    token = _isolated_context.set(context_id)
    try:
        yield
    finally:
        _isolated_context.reset(token)


def isolated_context() -> str | None:
    return _isolated_context.get()


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

    context_id = isolated_context()
    if context_id:
        env = isolation.isolate_env(env, context_id)
    return env


def child_command(command: list[str]) -> list[str]:
    """`command`, wrapped in a private session bus when the launch is isolated."""
    return isolation.wrap(command) if isolated_context() else command


FIELD_CODES = ("%f", "%F", "%u", "%U", "%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m")


def desktop_command(info, uris: list[str] | None = None) -> list[str]:
    """A desktop entry's Exec line as an argument list.

    Field codes are dropped and the URIs appended, which is what they expand to
    for the only two cases Context produces: no arguments, or a list of them.
    """
    raw = info.get_commandline() or ""
    parts = [p for p in shlex.split(raw) if p not in FIELD_CODES]
    return [*parts, *(uris or [])]


def launch_desktop_entry(app_id: str, uris: list[str] | None = None) -> None:
    try:
        info = Gio.DesktopAppInfo.new(app_id)
    except TypeError as exc:
        raise LookupError(f"no desktop entry for {app_id}") from exc
    if info is None:
        raise LookupError(f"no desktop entry for {app_id}")

    if isolated_context():
        # Gio launches the entry itself, so there is nowhere to put the private
        # bus: `dbus-run-session` has to be the parent of the process. Spawning
        # the Exec line directly is the only way to wrap it.
        command = desktop_command(info, uris)
        if not command:
            raise LookupError(f"{app_id} has no command to run")
        try:
            subprocess.Popen(
                child_command(command),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=child_env(),
            )
        except OSError as exc:
            raise LookupError(f"could not start {app_id}: {exc}") from exc
        return

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

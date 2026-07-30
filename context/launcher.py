"""Instantiating a context: placing it in a workspace and launching its apps."""

from __future__ import annotations

from dataclasses import dataclass, field

from gi.repository import GLib

from . import adapters, backends
from .backends import Backend, Workspace
from .store import Context


@dataclass
class LaunchResult:
    launched: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    backend: str = "none"
    workspace: str | None = None
    reused_workspace: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed


@dataclass
class CloseResult:
    closed: int = 0
    backend: str = "none"
    was_open: bool = False
    workspace_removed: bool = False


def context_is_open(ctx: Context, backend: Backend | None = None) -> bool:
    wm: Backend = backend or backends.detect()
    handle = ctx.handle_for(wm.name)
    if handle is None:
        return False
    return wm.workspace_exists(handle) and wm.window_count(handle) != 0


def close_context(ctx: Context, backend: Backend | None = None) -> CloseResult:
    """Shut a context down without forgetting it.

    The context keeps its definition and its workspace handle, so reopening it
    rebuilds the same workspace and relaunches its apps.
    """
    wm: Backend = backend or backends.detect()
    result = CloseResult(backend=wm.name)

    handle = ctx.handle_for(wm.name)
    if handle is None or not wm.workspace_exists(handle):
        return result

    result.was_open = True
    result.closed = wm.close_workspace(handle)

    # Windows close asynchronously, so this only succeeds once they are gone —
    # otherwise the workspace is left in place and reclaimed on the next close.
    if wm.remove_workspace(handle):
        result.workspace_removed = True
        ctx.workspaces.pop(wm.name, None)

    return result


def launch_app(app_id: str) -> None:
    adapters.launch_desktop_entry(app_id)


def _launch_resources(ctx: Context) -> tuple[list[str], list[tuple[str, str]]]:
    launched: list[str] = []
    failed: list[tuple[str, str]] = []
    for resource in ctx.resources:
        try:
            adapters.adapter_for(resource).launch(resource, ctx.id)
            launched.append(resource.app_id)
        except (GLib.Error, LookupError, OSError) as exc:
            failed.append((resource.app_id, str(exc)))
    return launched, failed


def launch_context(
    ctx: Context,
    backend: Backend | None = None,
    use_workspaces: bool = True,
) -> LaunchResult:
    wm: Backend = backend or (backends.detect() if use_workspaces else backends.NullBackend())
    result = LaunchResult(backend=wm.name)

    workspace: Workspace | None = None
    if use_workspaces:
        workspace = wm.ensure_workspace(ctx.title, ctx.handle_for(wm.name))

    if workspace is not None:
        ctx.set_handle(wm.name, workspace.handle)
        result.workspace = workspace.handle
        wm.switch_to(workspace)
        wm.prepare_launch(workspace)

        # An existing workspace may still be empty — a closed context keeps its
        # handle, and Cinnamon workspaces outlive their windows. Only skip
        # launching when something is actually there.
        if not workspace.created and wm.window_count(workspace.handle) != 0:
            result.reused_workspace = True
            return result

    result.launched, result.failed = _launch_resources(ctx)
    return result

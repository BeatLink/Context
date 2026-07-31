"""Instantiating a context: placing it in a workspace and launching its apps."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from gi.repository import GLib

from . import adapters, backends
from .backends import Backend, Workspace
from .layout import split_directions
from .store import Context

# How long to wait for launched windows to map before placing them.
LAYOUT_TIMEOUT = 8.0
# How long to wait for one launched window to map before launching the next.
WINDOW_TIMEOUT = 10.0


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


def active_context(contexts, backend: Backend | None = None):
    """The context whose workspace is focused right now, if any."""
    wm: Backend = backend or backends.detect()
    handle = wm.current_handle()
    if handle is None:
        return None
    for ctx in contexts:
        if ctx.handle_for(wm.name) == handle:
            return ctx
    return None


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


def _launch_resources(
    ctx: Context, wm: Backend | None = None, handle: str | None = None
) -> tuple[list[str], list[tuple[str, str]]]:
    """Launch the context's apps, tiling each one as it opens.

    A tiling compositor decides placement when a window maps, not afterwards, so
    the split direction is set before each launch and the window is waited for
    before moving on. Getting this wrong just means the compositor's default
    placement, never a broken layout.
    """
    launched: list[str] = []
    failed: list[tuple[str, str]] = []

    directions = split_directions(ctx.layout.slots) if ctx.layout.slots else []
    preselect = getattr(wm, "preselect", None) if wm is not None else None

    for index, resource in enumerate(ctx.resources):
        # The first window has nothing to split; every later one opens beside
        # the previous, in the direction the layout implies.
        if preselect is not None and 0 < index <= len(directions):
            preselect(directions[index - 1])

        before = wm.window_count(handle) if (wm and handle) else 0
        try:
            adapters.adapter_for(resource).launch(resource, ctx.id)
            launched.append(resource.app_id)
        except (GLib.Error, LookupError, OSError) as exc:
            failed.append((resource.app_id, str(exc)))
            continue

        if wm is not None and handle is not None:
            _await_window(wm, handle, before + 1)

    return launched, failed


def _await_window(wm: Backend, handle: str, expected: int) -> bool:
    """Wait for a launched window to map, so the next preselect applies to it."""
    deadline = time.monotonic() + WINDOW_TIMEOUT
    while time.monotonic() < deadline:
        if wm.window_count(handle) >= expected:
            return True
        time.sleep(0.2)
    return False


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

    result.launched, result.failed = _launch_resources(
        ctx, wm, workspace.handle if workspace is not None else None
    )

    return result


def _launch_resources(
    ctx: Context, wm: Backend | None = None, handle: str | None = None
) -> tuple[list[str], list[tuple[str, str]]]:
    """Launch the context's apps, tiling each one as it opens.

    A tiling compositor decides placement when a window maps, not afterwards, so
    the split direction is set before each launch and the window is waited for
    before moving on. Getting this wrong just means the compositor's default
    placement, never a broken layout.
    """
    launched: list[str] = []
    failed: list[tuple[str, str]] = []

    directions = split_directions(ctx.layout.slots) if ctx.layout.slots else []
    preselect = getattr(wm, "preselect", None) if wm is not None else None

    for index, resource in enumerate(ctx.resources):
        # The first window has nothing to split; every later one opens beside
        # the previous, in the direction the layout implies.
        if preselect is not None and 0 < index <= len(directions):
            preselect(directions[index - 1])

        before = wm.window_count(handle) if (wm and handle) else 0
        try:
            adapters.adapter_for(resource).launch(resource, ctx.id)
            launched.append(resource.app_id)
        except (GLib.Error, LookupError, OSError) as exc:
            failed.append((resource.app_id, str(exc)))
            continue

        if wm is not None and handle is not None:
            _await_window(wm, handle, before + 1)

    return launched, failed


def _await_window(wm: Backend, handle: str, expected: int) -> bool:
    """Wait for a launched window to map, so the next preselect applies to it."""
    deadline = time.monotonic() + WINDOW_TIMEOUT
    while time.monotonic() < deadline:
        if wm.window_count(handle) >= expected:
            return True
        time.sleep(0.2)
    return False


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

    result.launched, result.failed = _launch_resources(
        ctx, wm, workspace.handle if workspace is not None else None
    )

    if workspace is not None and ctx.layout.slots:
        # Windows map asynchronously, so give them a moment to appear before
        # placing them. Anything still missing keeps the layout's default.
        result.placed = _apply_layout(wm, workspace.handle, ctx)

    return result


def _await_window(wm: Backend, handle: str, expected: int) -> bool:
    """Wait for a launched window to map, so the next preselect applies to it."""
    deadline = time.monotonic() + WINDOW_TIMEOUT
    while time.monotonic() < deadline:
        if wm.window_count(handle) >= expected:
            return True
        time.sleep(0.2)
    return False


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

    result.launched, result.failed = _launch_resources(
        ctx, wm, workspace.handle if workspace is not None else None
    )

    return result


def _launch_resources(
    ctx: Context, wm: Backend | None = None, handle: str | None = None
) -> tuple[list[str], list[tuple[str, str]]]:
    """Launch the context's apps, tiling each one as it opens.

    A tiling compositor decides placement when a window maps, not afterwards, so
    the split direction is set before each launch and the window is waited for
    before moving on. Getting this wrong just means the compositor's default
    placement, never a broken layout.
    """
    launched: list[str] = []
    failed: list[tuple[str, str]] = []

    directions = split_directions(ctx.layout.slots) if ctx.layout.slots else []
    preselect = getattr(wm, "preselect", None) if wm is not None else None

    for index, resource in enumerate(ctx.resources):
        # The first window has nothing to split; every later one opens beside
        # the previous, in the direction the layout implies.
        if preselect is not None and 0 < index <= len(directions):
            preselect(directions[index - 1])

        before = wm.window_count(handle) if (wm and handle) else 0
        try:
            adapters.adapter_for(resource).launch(resource, ctx.id)
            launched.append(resource.app_id)
        except (GLib.Error, LookupError, OSError) as exc:
            failed.append((resource.app_id, str(exc)))
            continue

        if wm is not None and handle is not None:
            _await_window(wm, handle, before + 1)

    return launched, failed


def _await_window(wm: Backend, handle: str, expected: int) -> bool:
    """Wait for a launched window to map, so the next preselect applies to it."""
    deadline = time.monotonic() + WINDOW_TIMEOUT
    while time.monotonic() < deadline:
        if wm.window_count(handle) >= expected:
            return True
        time.sleep(0.2)
    return False


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

    result.launched, result.failed = _launch_resources(
        ctx, wm, workspace.handle if workspace is not None else None
    )

    if workspace is not None and ctx.layout.slots:
        # Windows map asynchronously, so give them a moment to appear before
        # placing them. Anything still missing keeps the layout's default.
        result.placed = _apply_layout(wm, workspace.handle, ctx)

    return result


def _apply_layout(wm: Backend, handle: str, ctx: Context) -> int:
    apply = getattr(wm, "apply_layout", None)
    if apply is None:
        return 0

    expected = len(ctx.resources)
    deadline = time.monotonic() + LAYOUT_TIMEOUT
    while time.monotonic() < deadline:
        if wm.window_count(handle) >= expected:
            break
        time.sleep(0.25)

    return apply(handle, ctx.layout.slots)

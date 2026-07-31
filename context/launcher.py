"""Instantiating a context: placing it in a workspace and launching its apps."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from gi.repository import GLib

from . import adapters, backends, isolation
from .backends import Backend, Workspace
from .layout import split_directions
from .logging_setup import get_logger, traced
from .store import Context

log = get_logger("launcher")

# How long to wait for one launched window to map before launching the next.
WINDOW_TIMEOUT = 10.0


@dataclass
class LaunchResult:
    launched: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    backend: str = "none"
    workspace: str | None = None
    reused_workspace: bool = False
    resized: int = 0
    layout_repaired: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed


@dataclass
class CloseResult:
    closed: int = 0
    backend: str = "none"
    was_open: bool = False
    workspace_removed: bool = False


@traced(log)
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


@traced(log)
def reconnect(contexts, backend: Backend | None = None) -> list:
    """Re-adopt contexts whose workspaces are still running.

    Context can be restarted — a crash, an update, a manual relaunch — while the
    windows it opened carry on. Without this the launcher comes back believing
    nothing is open, offers to launch contexts that are already there, and
    duplicates them.

    A context is reconnected when its stored handle still names a workspace with
    windows in it; one whose windows have since been closed has its handle
    dropped, so the next launch rebuilds it rather than reusing an empty
    workspace.
    """
    wm: Backend = backend or backends.detect()
    live = []
    for ctx in contexts:
        handle = ctx.handle_for(wm.name)
        if handle is None:
            continue
        if wm.workspace_exists(handle) and wm.window_count(handle) > 0:
            live.append(ctx)
            log.info("reconnected to %s on %s", ctx.title, handle)
        else:
            # The workspace is gone or empty: the handle is stale.
            ctx.workspaces.pop(wm.name, None)
            log.debug("dropped stale handle for %s", ctx.title)
    return live


@traced(log)
def open_state(contexts, backend: Backend | None = None) -> tuple[set[str], str | None]:
    """Which contexts are open, and which one is focused, in two queries.

    The launcher polls this every couple of seconds. Asking `context_is_open`
    per context instead costs two subprocess calls each, which is enough work on
    the main loop to be felt with only a handful of contexts.
    """
    wm: Backend = backend or backends.detect()
    live = wm.live_handles()
    current = wm.current_handle()

    open_ids = set()
    active_id = None
    for ctx in contexts:
        handle = ctx.handle_for(wm.name)
        if handle is None:
            continue
        if handle in live:
            open_ids.add(ctx.id)
        if current is not None and handle == current:
            active_id = ctx.id
    return open_ids, active_id


@traced(log)
def context_is_open(ctx: Context, backend: Backend | None = None) -> bool:
    wm: Backend = backend or backends.detect()
    handle = ctx.handle_for(wm.name)
    if handle is None:
        return False
    return wm.workspace_exists(handle) and wm.window_count(handle) != 0


@traced(log)
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


@traced(log)
def launch_context(
    ctx: Context,
    backend: Backend | None = None,
    use_workspaces: bool = True,
) -> LaunchResult:
    wm: Backend = backend or (
        backends.detect() if use_workspaces else backends.NullBackend()
    )
    result = LaunchResult(backend=wm.name)

    workspace: Workspace | None = None
    if use_workspaces:
        workspace = wm.ensure_workspace(ctx.title, ctx.handle_for(wm.name))

    if workspace is not None:
        ctx.set_handle(wm.name, workspace.handle)
        result.workspace = workspace.handle
        wm.switch_to(workspace)
        wm.prepare_launch(workspace)

        # An existing workspace may still be empty: a closed context keeps its
        # handle. Only skip launching when something is actually there.
        if not workspace.created and wm.window_count(workspace.handle) != 0:
            result.reused_workspace = True
            return result

    # Repair the layout before using it. A hand-edited contexts.json, or a
    # context whose apps changed without its slots, would otherwise either tile
    # into nonsense or launch nothing at all.
    # Always take the healed layout, not only when something was wrong: a
    # context with no layout at all gets one, so every launch lands in a known
    # arrangement rather than wherever the compositor happens to put things.
    healed, problems = ctx.layout.healed(len(ctx.resources))
    ctx.layout = healed
    if problems:
        for problem in problems:
            log.warning("layout for %s %s; repaired", ctx.title, problem)
        result.layout_repaired = True

    handle = workspace.handle if workspace is not None else None
    result.launched, result.failed = _launch_resources(ctx, wm, handle)

    # preselect only chooses a side, so every split starts even. Correct the
    # proportions once all the windows are up.
    # Nothing to proportion with a single window: it fills the workspace.
    if handle is not None and len(ctx.layout.slots) > 1:
        ratios = getattr(wm, "apply_ratios", None)
        if ratios is not None:
            result.resized = ratios(handle, ctx.layout.slots)

    return result


def _isolation_for(ctx: Context, resource) -> str | None:
    """The context id to isolate this resource under, or None for a normal launch.

    Both have to agree. The context opts in, and an application can opt out of
    it — one that keeps a shared database must not be started twice without the
    copies knowing about each other, and not knowing is exactly what isolation
    produces.
    """
    if not (ctx.isolated and resource.isolate):
        return None
    if not isolation.available():
        log.warning(
            "%s asks for isolation but dbus-run-session is missing", ctx.title
        )
        return None
    return ctx.id


@traced(log)
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
            with adapters.isolating(_isolation_for(ctx, resource)):
                adapters.adapter_for(resource).launch(resource, ctx.id)
            launched.append(resource.app_id)
        except (GLib.Error, LookupError, OSError) as exc:
            failed.append((resource.app_id, str(exc)))
            continue

        if wm is not None and handle is not None:
            if not _await_window(wm, handle, before + 1):
                log.warning(
                    "%s did not map within %.0fs; later windows may tile oddly",
                    resource.app_id, WINDOW_TIMEOUT,
                )

    return launched, failed


def _await_window(wm: Backend, handle: str, expected: int) -> bool:
    """Wait for a launched window to map, so the next preselect applies to it."""
    deadline = time.monotonic() + WINDOW_TIMEOUT
    while time.monotonic() < deadline:
        if wm.window_count(handle) >= expected:
            return True
        time.sleep(0.2)
    return False

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
    # One handle per screen the context spans, primary first.
    screens: list[str] = field(default_factory=list)
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
        if handle in ctx.handles_for(wm.name):
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
        handles = ctx.handles_for(wm.name)
        if not handles:
            continue
        # A context spanning screens is still running when any one of its
        # workspaces has windows — closing one screen does not close it.
        running = [
            h for h in handles if wm.workspace_exists(h) and wm.window_count(h) > 0
        ]
        if running:
            live.append(ctx)
            log.info("reconnected to %s on %s", ctx.title, ", ".join(running))
        else:
            # Every workspace is gone or empty: the handles are all stale.
            ctx.drop_handles(wm.name)
            log.debug("dropped stale handles for %s", ctx.title)
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
        handles = ctx.handles_for(wm.name)
        if not handles:
            continue
        # Open when any screen has windows; active when you are looking at any
        # of them, since a context spanning two screens is one place.
        if any(h in live for h in handles):
            open_ids.add(ctx.id)
        if current is not None and current in handles:
            active_id = ctx.id
    return open_ids, active_id


@traced(log)
def context_is_open(ctx: Context, backend: Backend | None = None) -> bool:
    wm: Backend = backend or backends.detect()
    return any(
        wm.workspace_exists(h) and wm.window_count(h) != 0
        for h in ctx.handles_for(wm.name)
    )


@traced(log)
def close_context(ctx: Context, backend: Backend | None = None) -> CloseResult:
    """Shut a context down without forgetting it.

    The context keeps its definition and its workspace handle, so reopening it
    rebuilds the same workspace and relaunches its apps.
    """
    wm: Backend = backend or backends.detect()
    result = CloseResult(backend=wm.name)

    handles = [h for h in ctx.handles_for(wm.name) if wm.workspace_exists(h)]
    if not handles:
        return result

    result.was_open = True
    # Every screen: closing one and leaving the other running would be a
    # context half-open, which is not a state the launcher can show.
    for handle in handles:
        result.closed += wm.close_workspace(handle)

    # Windows close asynchronously, so this only succeeds once they are gone —
    # otherwise the workspace is left in place and reclaimed on the next close.
    if all(wm.remove_workspace(h) for h in handles):
        result.workspace_removed = True
        ctx.drop_handles(wm.name)

    return result


def launch_app(app_id: str) -> None:
    adapters.launch_desktop_entry(app_id)


def screen_handle(primary: str, screen: int) -> str:
    """The handle for one of a context's screens.

    Derived from the primary handle rather than the title, so renaming a
    context still cannot orphan a workspace — the invariant that matters here.
    """
    return primary if screen == 0 else f"{primary}-s{screen + 1}"


@traced(log)
def launch_context(
    ctx: Context,
    backend: Backend | None = None,
    use_workspaces: bool = True,
) -> LaunchResult:
    """Open a context across every screen it arranges itself for.

    One workspace per screen, each with its own slots. The arrangement is
    chosen by how many screens are attached now, so a context laid out for two
    monitors opens as a single-screen context when undocked without losing the
    two-screen version.
    """
    wm: Backend = backend or (
        backends.detect() if use_workspaces else backends.NullBackend()
    )
    result = LaunchResult(backend=wm.name)

    if not use_workspaces:
        result.launched, result.failed = _launch_resources(ctx, wm, None)
        return result

    outputs = _outputs(wm)
    arrangement, problems = ctx.arrangement_for(len(outputs)).healed(len(ctx.resources))
    ctx.set_arrangement(len(outputs), arrangement)
    if problems:
        for problem in problems:
            log.warning("layout for %s %s; repaired", ctx.title, problem)
        result.layout_repaired = True

    primary = wm.ensure_workspace(ctx.title, ctx.handle_for(wm.name))
    if primary is None:
        result.launched, result.failed = _launch_resources(ctx, wm, None)
        return result
    result.workspace = primary.handle

    # Every screen the arrangement uses, and no more than there are outputs.
    screens = min(arrangement.screen_count, max(1, len(outputs)))
    reused = True
    for screen in range(screens):
        handle = screen_handle(primary.handle, screen)
        ctx.set_handle(wm.name, handle, screen=screen)
        result.screens.append(handle)

        workspace = wm.ensure_workspace(ctx.title, handle)
        if workspace is None:
            continue
        wm.switch_to(workspace)
        # Only after switching: a named workspace does not exist until
        # something opens it, and binding one that does not exist fails.
        if screen < len(outputs):
            wm.place_workspace(handle, outputs[screen].name)
        wm.prepare_launch(workspace)

        # An existing workspace may still be empty: a closed context keeps its
        # handle. Only skip launching when something is actually there.
        if workspace.created or wm.window_count(handle) == 0:
            reused = False
            launched, failed = _launch_resources(
                ctx, wm, handle, arrangement.indices_on(screen),
                arrangement.layout_for(screen),
            )
            result.launched.extend(launched)
            result.failed.extend(failed)

            slots = arrangement.layout_for(screen).slots
            if len(slots) > 1:
                ratios = getattr(wm, "apply_ratios", None)
                if ratios is not None:
                    result.resized += ratios(handle, slots)

    result.reused_workspace = reused
    # Finish on the context's first screen rather than wherever the last one
    # happened to be, so opening a context leaves you looking at its main work.
    if screens > 1:
        wm.switch_to(Workspace(handle=primary.handle, label=ctx.title))
    return result


def _outputs(wm: Backend):
    """The screens available, in the compositor's order.

    Order matters and is not arbitrary: screen 0 is the context's primary, and
    an arrangement stored for two screens has to mean the same two next time.
    """
    try:
        found = list(wm.monitors())
    except OSError as exc:
        log.warning("could not read monitors: %s", exc)
        found = []
    if not found:
        return []
    # Focused first, so a context's primary screen is the one being used.
    return sorted(found, key=lambda m: (not m.focused, m.x, m.y))


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
    ctx: Context,
    wm: Backend | None = None,
    handle: str | None = None,
    indices: list[int] | None = None,
    layout=None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Launch some of the context's apps, tiling each one as it opens.

    `indices` is which resources belong on this screen; None means all of them,
    which is what a context that does not span uses.

    A tiling compositor decides placement when a window maps, not afterwards, so
    the split direction is set before each launch and the window is waited for
    before moving on. Getting this wrong just means the compositor's default
    placement, never a broken layout.
    """
    launched: list[str] = []
    failed: list[tuple[str, str]] = []

    wanted = list(range(len(ctx.resources))) if indices is None else list(indices)
    slots = (layout or ctx.layout).slots
    directions = split_directions(slots) if slots else []
    preselect = getattr(wm, "preselect", None) if wm is not None else None

    for index, resource_index in enumerate(wanted):
        if not 0 <= resource_index < len(ctx.resources):
            continue
        resource = ctx.resources[resource_index]
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

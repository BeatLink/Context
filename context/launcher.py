"""Instantiating a context: placing it in a workspace and launching its apps."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from gi.repository import GLib

from . import adapters, backends, isolation
from .backends import Backend, Workspace
from .layout import split_directions
from .logging_setup import get_logger, traced
from .resources import Resource
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
    """The screens available, in a stable left-to-right order.

    Ordered by position, never by focus. "Screen 1" has to mean the same
    physical monitor every time or the arrangement is not honoured: an app the
    user put on their right-hand display would open on the left whenever they
    happened to launch from there.

    Position also survives a replug, which a connector name does not — the same
    monitor on a different port keeps its place in the row.
    """
    try:
        found = list(wm.monitors())
    except OSError as exc:
        log.warning("could not read monitors: %s", exc)
        found = []
    return sorted(found, key=lambda m: (m.x, m.y, m.name))


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


# -- window management -------------------------------------------------------


@traced(log)
def move_window_to_context(
    window_id: str,
    ctx: Context,
    screen: int = 0,
    backend: Backend | None = None,
) -> bool:
    """Send a window into a context, on one of its screens.

    The context has to have that screen already — a window cannot be sent to a
    workspace nothing has opened, since a named workspace does not exist until
    something is on it. Falls back to the primary, which always exists once the
    context has been launched.
    """
    wm: Backend = backend or backends.detect()
    handle = ctx.handle_for(wm.name, screen) or ctx.handle_for(wm.name)
    if handle is None:
        log.info("%s has no workspace yet; open it first", ctx.title)
        return False
    log.info("moving %s into %s", window_id, handle)
    return wm.move_window(window_id, handle)


@traced(log)
def move_window_to_screen(
    window_id: str,
    ctx: Context,
    direction: int,
    backend: Backend | None = None,
) -> bool:
    """Throw a window to the context's next or previous screen.

    Stays inside the context: this is for rearranging what a context already
    owns, not for moving between contexts.
    """
    wm: Backend = backend or backends.detect()
    handles = ctx.handles_for(wm.name)
    if len(handles) < 2:
        return False
    current = next(
        (w.handle for w in wm.windows() if w.id == window_id), None
    )
    if current not in handles:
        return False
    target = handles.index(current) + direction
    if not 0 <= target < len(handles):
        return False
    return wm.move_window(window_id, handles[target])


@traced(log)
def unmanaged_windows(contexts, backend: Backend | None = None) -> list:
    """Open windows that belong to no context.

    The goal state is that every window belongs to one; this is what is left
    over. A window on a workspace no context claims is unmanaged, whoever
    opened it.
    """
    wm: Backend = backend or backends.detect()
    claimed = {h for ctx in contexts for h in ctx.handles_for(wm.name)}
    return [w for w in wm.windows() if w.handle not in claimed]


@traced(log)
def capture_arrangement(
    ctx: Context, backend: Backend | None = None
) -> tuple[int, int]:
    """Save what a context has become back into its arrangement.

    A context drifts as it is used — windows are opened, closed and moved. This
    reads the live positions and makes them the arrangement for the current
    screen count, so the definition tracks reality rather than staying a
    snapshot of the day it was written.

    Returns (windows captured, screens captured).
    """
    from .arrangement import Arrangement
    from .layout import Layout, Slot

    wm: Backend = backend or backends.detect()
    handles = ctx.handles_for(wm.name)
    if not handles:
        return 0, 0

    # By id, because that is what a window reports. Taking the monitor from the
    # screen index instead was wrong whenever a context's screen 0 was not
    # monitor 0 — every slot came out at x=1.0, off the right-hand edge.
    by_id = {m.id: m for m in _outputs(wm)}
    outputs = _outputs(wm)
    screens: list[Layout] = []
    assignments: dict[int, int] = {}
    resources: list = []

    for screen, handle in enumerate(handles):
        clients = _clients_on(wm, handle)
        slots = []
        fallback = outputs[screen] if screen < len(outputs) else None
        for client in clients:
            monitor = by_id.get(client.get("monitor_id"), fallback)
            slots.append(_slot_from(client, monitor))
            assignments[len(resources)] = screen
            existing = ctx.resource_for(client.get("app_id", ""))
            resources.append(
                existing if existing is not None else Resource(app_id=client["app_id"])
            )
        screens.append(Layout(slots=slots))

    if not resources:
        return 0, 0

    ctx.resources = resources
    ctx.set_arrangement(
        len(handles), Arrangement(screens=screens, assignments=assignments)
    )
    return len(resources), len(screens)


def _clients_on(wm: Backend, handle: str) -> list[dict]:
    """Live geometry per window, for whatever the backend can report."""
    reader = getattr(wm, "client_geometry", None)
    if reader is None:
        return []
    return reader(handle)


def _slot_from(client: dict, monitor) -> "Slot":
    """One window's position as fractions of the screen it is on."""
    from .layout import Slot

    width = getattr(monitor, "width", 0) or 1920
    height = getattr(monitor, "height", 0) or 1080
    origin_x = getattr(monitor, "x", 0)
    origin_y = getattr(monitor, "y", 0)

    x = (client.get("x", 0) - origin_x) / width
    y = (client.get("y", 0) - origin_y) / height
    return Slot(
        x=min(1.0, max(0.0, x)),
        y=min(1.0, max(0.0, y)),
        width=min(1.0, max(0.05, client.get("width", width) / width)),
        height=min(1.0, max(0.05, client.get("height", height) / height)),
    )

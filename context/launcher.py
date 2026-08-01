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


# The stand-in for everything that belongs nowhere. Not a stored context and
# never written to the file: it exists for as long as there are windows outside
# every context, and stops existing when they have a home.
NO_CONTEXT_ID = "virtual:no-context"
NO_CONTEXT_TITLE = "No context"


def loose_context(loose: list[dict]) -> Context | None:
    """The windows belonging to no context, as something a list can show.

    A context is what Context knows how to display and act on, so the loose
    windows are given one rather than a list growing a second kind of row. It
    carries the windows themselves on `windows`, since closing and saving them
    need more than their applications.
    """
    if not loose:
        return None
    ctx = Context(
        title=NO_CONTEXT_TITLE,
        id=NO_CONTEXT_ID,
        resources=[Resource(app_id=w.get("app_id", "")) for w in loose if w.get("app_id")],
    )
    ctx.windows = list(loose)
    return ctx


def is_no_context(ctx) -> bool:
    return getattr(ctx, "id", None) == NO_CONTEXT_ID


@traced(log)
def close_loose(loose: list[dict], backend: Backend | None = None) -> int:
    """Close every window that belongs to no context. Returns how many were asked."""
    wm: Backend = backend or backends.detect()
    closed = 0
    for window in loose:
        window_id = window.get("id")
        if window_id and wm.close_window(window_id):
            closed += 1
    return closed


@traced(log)
def adopt_loose(
    ctx: Context, loose: list[dict], backend: Backend | None = None
) -> int:
    """Move every loose window into `ctx`, so the context is what they became.

    Saving the no-context is not a snapshot of where those windows happen to be
    — they are scattered across whatever workspaces they opened on. They are
    gathered into the new context's workspace, which is the only place their
    positions mean anything, and captured from there.
    """
    wm: Backend = backend or backends.detect()
    workspace = wm.ensure_workspace(ctx.title, ctx.handle_for(wm.name))
    if workspace is None:
        return 0
    ctx.set_handle(wm.name, workspace.handle)

    moved = 0
    for window in loose:
        window_id = window.get("id")
        if window_id and wm.move_window(window_id, workspace.handle):
            moved += 1
    if moved:
        # Their geometry only settles once the compositor has tiled them on the
        # workspace they are now on, which needs it to be the one on screen.
        wm.switch_to(workspace)
        capture_arrangement(ctx, backend=wm)
    return moved


@dataclass(frozen=True)
class LiveState:
    """Everything the launcher's list needs to know about what is running.

    Read in one pass rather than a query per question: the list is refreshed on
    a timer, and each answer costs a `hyprctl` call — which is subprocess work
    on the GTK main loop.
    """

    open_ids: set[str] = field(default_factory=set)
    active_id: str | None = None
    # Contexts whose windows no longer match what was saved.
    drifted_ids: set[str] = field(default_factory=set)
    # Windows belonging to no context, as geometry dictionaries.
    loose: list[dict] = field(default_factory=list)

    @property
    def signature(self) -> tuple:
        """What has to change for the list to be worth rebuilding."""
        return (
            frozenset(self.open_ids),
            self.active_id,
            frozenset(self.drifted_ids),
            tuple(sorted(w.get("id", "") for w in self.loose)),
        )


@traced(log)
def read_live_state(contexts, backend: Backend | None = None) -> LiveState:
    """Which contexts are open, which is focused, which have drifted, and what
    belongs to none of them."""
    wm: Backend = backend or backends.detect()
    open_ids, active_id = open_state(contexts, backend=wm)

    reader = getattr(wm, "geometry_by_handle", None)
    geometry = reader() if reader is not None else {}
    if not geometry:
        return LiveState(open_ids=open_ids, active_id=active_id)

    claimed = {h for ctx in contexts for h in ctx.handles_for(wm.name)}
    loose = [
        window
        for handle, windows in geometry.items()
        if handle not in claimed
        for window in windows
    ]

    drifted = set()
    for ctx in contexts:
        handles = ctx.handles_for(wm.name)
        # Only what is running: a closed context cannot have drifted, and
        # comparing one against no windows would say every one had.
        if not handles or not any(geometry.get(handle) for handle in handles):
            continue
        if _drifted(ctx, handles, geometry):
            drifted.add(ctx.id)

    return LiveState(
        open_ids=open_ids, active_id=active_id, drifted_ids=drifted, loose=loose
    )


@traced(log)
def hand_keyboard_back(backend: Backend | None = None) -> None:
    """Focus the window the compositor still counts as focused.

    When a layer surface lets the keyboard go, Hyprland reports the window
    underneath as active again without re-sending the keyboard enter, so the
    seat routes typing nowhere until some *other* window is focused and the
    original clicked back into. Focusing the most recent window explicitly is
    that recovery, performed deliberately instead of by the user.
    """
    wm: Backend = backend or backends.detect()
    found = wm.windows(wm.current_handle())
    if found:
        # Without the warp: this runs as the pointer leaves the sidebar, and
        # a cursor snapped into the window mid-gesture is worse than the dead
        # keyboard it fixes.
        wm.focus_window(found[0].id, warp=False)


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

        # What this screen is missing, rather than whether it holds anything.
        #
        # Skipping a screen that held *any* window meant an application closed
        # by hand never came back: reopening a context with two of its three
        # windows up relaunched nothing, because the workspace was not empty.
        wanted = arrangement.indices_on(screen)
        missing = _missing_on(ctx, wm, handle, wanted)
        if not missing:
            continue

        reused = False
        launched, failed = _launch_resources(
            ctx, wm, handle, missing, arrangement.layout_for(screen)
        )
        result.launched.extend(launched)
        result.failed.extend(failed)

        # Only worth proportioning a screen that was built from nothing. A
        # window added beside existing ones is placed by the compositor, and
        # resizing everything would rearrange what the user already had.
        slots = arrangement.layout_for(screen).slots
        if len(missing) == len(wanted) and len(slots) > 1:
            ratios = getattr(wm, "apply_ratios", None)
            if ratios is not None:
                result.resized += ratios(handle, slots)

    result.reused_workspace = reused
    # Finish on the context's first screen rather than wherever the last one
    # happened to be, so opening a context leaves you looking at its main work.
    if screens > 1:
        wm.switch_to(Workspace(handle=primary.handle, label=ctx.title))
    return result


def _missing_on(
    ctx: Context, wm: Backend, handle: str, wanted: list[int]
) -> list[int]:
    """Which of `wanted` has no window on this screen yet.

    Counted per application, so a context asking for two terminals still gets
    two. There is no way to tell *which* window belongs to which resource — see
    ROADMAP §3 — so this is the closest honest answer: how many of each
    application should be here, minus how many are.
    """
    from collections import Counter

    present = Counter(
        window.app_id.strip().casefold()
        for window in wm.windows(handle)
        if window.app_id
    )

    missing = []
    for index in wanted:
        if not 0 <= index < len(ctx.resources):
            continue
        app = ctx.resources[index].app_id.strip().casefold()
        # A window's class rarely matches the desktop id exactly, so both
        # forms count: `element` for `element.desktop`.
        for key in (app, app.removesuffix(".desktop")):
            if present.get(key):
                present[key] -= 1
                break
        else:
            missing.append(index)
    return missing


def _outputs(wm: Backend):
    """The screens available, as screen 1, screen 2 and so on.

    Never ordered by focus. "Screen 2" has to mean the same physical monitor
    every time or the arrangement is not honoured: an app the user put on their
    right-hand display would open on the left whenever they happened to launch
    from there.

    The order itself is a setting — see `monitors.ordered` — so a context only
    ever refers to a screen by number.
    """
    from . import monitors

    return monitors.ordered(wm)


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
            if _await_window(wm, handle, before + 1):
                _finish_launch(ctx, resource)
            elif _adopt_raised_window(wm, handle, resource):
                _finish_launch(ctx, resource)
            else:
                # Without the window there is nothing safe to finish into:
                # Firefox's remaining tabs would land in whatever is focused.
                log.warning(
                    "%s did not map within %.0fs; later windows may tile oddly",
                    resource.app_id, WINDOW_TIMEOUT,
                )
        else:
            _finish_launch(ctx, resource)

    return launched, failed


def _adopt_raised_window(wm: Backend, handle: str, resource) -> bool:
    """Bring in the window a launch raised instead of opening.

    A single-instance app answers a launch by focusing the window it already
    has, wherever that is — the launch reports success and the context gets
    nothing. The app has declared that window its answer, so the context takes
    it: the most recently focused window of the same app, moved in. Matching
    is by window class against the desktop id, which is as precise as Wayland
    allows — see ROADMAP §3 for why pids cannot do better.
    """
    for window in wm.windows():
        if window.handle == handle:
            continue
        if _same_app(window, resource):
            wm.move_window(window.id, handle)
            log.info(
                "%s raised an existing window; moved it into %s",
                resource.app_id, handle,
            )
            return True
    return False


def _same_app(window, resource) -> bool:
    base = resource.app_id.strip().casefold().removesuffix(".desktop")
    cls = (window.app_id or "").strip().casefold()
    if not base or not cls:
        return False
    return base == cls or base in cls or cls in base


def _finish_launch(ctx: Context, resource) -> None:
    """The adapter's second act, once the launched window is up.

    Firefox's main-profile mode is why this exists: `--new-tab` lands in the
    focused window, so the rest of a resource's URLs must wait until the
    window `launch` opened is the one with the focus.
    """
    finish = getattr(adapters.adapter_for(resource), "finish_launch", None)
    if finish is None:
        return
    try:
        with adapters.isolating(_isolation_for(ctx, resource)):
            finish(resource, ctx.id)
    except (GLib.Error, LookupError, OSError) as exc:
        log.warning("finishing %s: %s", resource.app_id, exc)


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

    screens: list[Layout] = []
    assignments: dict[int, int] = {}
    resources: list = []

    for screen, handle in enumerate(handles):
        clients = _clients_on(wm, handle)
        box = tiled_box(clients)
        slots = []
        for client in clients:
            slots.append(_slot_from(client, box))
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


def tiled_box(clients: list[dict]) -> tuple[float, float, float, float]:
    """The rectangle the windows span, as (x, y, width, height).

    This is what a layout's fractions are *of*. Not the monitor: bars, the
    sidebar, the compositor's gaps and hyprbars' titlebars all sit between the
    panel and the windows, so a maximised window measured against the screen
    came out at 0.006 from the left and 0.911 tall — never equal to the 0,0,1,1
    it was launched from, so every context read as moved the moment anything
    reserved a different amount of space. The launch path already works in
    these terms: `apply_ratios` proportions the area the windows occupy.
    """
    if not clients:
        return (0.0, 0.0, 1.0, 1.0)
    lefts = [c.get("x", 0) for c in clients]
    tops = [c.get("y", 0) for c in clients]
    rights = [c.get("x", 0) + c.get("width", 0) for c in clients]
    bottoms = [c.get("y", 0) + c.get("height", 0) for c in clients]
    x, y = min(lefts), min(tops)
    return (x, y, max(1.0, max(rights) - x), max(1.0, max(bottoms) - y))


def _slot_from(client: dict, box: tuple[float, float, float, float]) -> "Slot":
    """One window's position as fractions of the area the windows span."""
    from .layout import Slot

    origin_x, origin_y, width, height = box
    x = (client.get("x", 0) - origin_x) / width
    y = (client.get("y", 0) - origin_y) / height
    return Slot(
        x=min(1.0, max(0.0, x)),
        y=min(1.0, max(0.0, y)),
        width=min(1.0, max(0.05, client.get("width", width) / width)),
        height=min(1.0, max(0.05, client.get("height", height) / height)),
    )


@traced(log)
def has_drifted(ctx: Context, backend: Backend | None = None) -> bool:
    """Whether a context's windows no longer match what was saved.

    Compares what is running against the stored arrangement: how many windows,
    which applications, and roughly where. Positions are compared loosely — a
    window a few pixels off is the compositor's rounding, not a change the user
    made, and prompting for that would make the offer worthless.
    """
    wm: Backend = backend or backends.detect()
    handles = ctx.handles_for(wm.name)
    if not handles:
        return False
    geometry = {handle: _clients_on(wm, handle) for handle in handles}
    return _drifted(ctx, handles, geometry)


@traced(log)
def drifted_ids(contexts, backend: Backend | None = None) -> set[str]:
    """The same question for every context, in two queries rather than two each.

    The launcher asks on every poll so a drifted context can offer to be saved
    where it is listed, and per-context queries put a `hyprctl clients` call per
    open context on the main loop every couple of seconds.
    """
    wm: Backend = backend or backends.detect()
    reader = getattr(wm, "geometry_by_handle", None)
    if reader is None:
        return set()
    geometry = reader()
    if not geometry:
        return set()

    drifted = set()
    for ctx in contexts:
        handles = ctx.handles_for(wm.name)
        # Only what is running: a closed context cannot have drifted, and
        # comparing one against no windows would say everything had.
        if not handles or not any(geometry.get(handle) for handle in handles):
            continue
        if _drifted(ctx, handles, geometry):
            drifted.add(ctx.id)
    return drifted


def _drifted(ctx: Context, handles, geometry: dict) -> bool:
    saved = ctx.arrangement_for(len(handles))

    for screen, handle in enumerate(handles):
        clients = geometry.get(handle) or []
        indices = saved.indices_on(screen)
        if len(clients) != len(indices):
            return True

        slots = saved.layout_for(screen).slots
        box = tiled_box(clients)
        for position, client in enumerate(clients):
            if position >= len(slots):
                return True
            if not _slots_match(_slot_from(client, box), slots[position]):
                return True
    return False


# Below this a difference is the compositor's rounding rather than a move: gaps,
# borders and integer pixels never divide evenly into a fraction of the screen.
DRIFT_TOLERANCE = 0.02


def _slots_match(live, saved) -> bool:
    return all(
        abs(getattr(live, field) - getattr(saved, field)) <= DRIFT_TOLERANCE
        for field in ("x", "y", "width", "height")
    )

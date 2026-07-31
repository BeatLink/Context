"""Launching and closing contexts.

Several of these cover bugs that reached a running desktop, and exist so they
cannot come back quietly.
"""

from __future__ import annotations

import pytest

from context import launcher
from context.layout import Layout, Slot, preset_for
from context.launcher import close_context, context_is_open, launch_context
from context.resources import Resource
from context.store import Context


@pytest.fixture
def ctx():
    return Context(
        title="work",
        resources=[
            Resource(app_id="a.desktop"),
            Resource(app_id="b.desktop"),
        ],
        layout=preset_for(2),
    )


@pytest.fixture(autouse=True)
def stub_adapters(monkeypatch, backend):
    """Record launches instead of starting real applications.

    The stub also puts a window on the workspace being launched into, the way
    a real application eventually would. Without that every launch waits out
    `WINDOW_TIMEOUT` for a window that is never coming, and the file takes
    minutes instead of milliseconds.
    """
    launched: list[str] = []

    class StubAdapter:
        def launch(self, resource, context_id):
            launched.append(resource.app_id)
            if backend.current:
                backend.add_window(backend.current)

    monkeypatch.setattr(launcher.adapters, "adapter_for", lambda r: StubAdapter())
    return launched


def test_creates_workspace_and_launches(ctx, backend, stub_adapters):
    result = launch_context(ctx, backend=backend)
    assert result.workspace == "ctx-work"
    assert stub_adapters == ["a.desktop", "b.desktop"]
    assert ctx.handle_for("fake") == "ctx-work"


def test_switches_before_launching(ctx, backend, stub_adapters):
    """Windows land on whatever is focused, so the switch has to come first."""
    launch_context(ctx, backend=backend)
    kinds = [c[0] for c in backend.calls]
    assert kinds.index("switch") < kinds.index("preselect")


def test_preselects_between_launches(ctx, backend, stub_adapters):
    """A tiling compositor places a window when it maps, not afterwards.

    So the order must be preselect, launch, preselect, launch — not all the
    preselects up front.
    """
    launch_context(ctx, backend=backend)
    assert backend.sequence("preselect") == [("preselect", "r")]


def test_reopening_a_live_context_does_not_relaunch(ctx, backend, stub_adapters):
    backend.place_windows("ctx-work", "a.desktop", "b.desktop")
    result = launch_context(ctx, backend=backend)
    assert result.reused_workspace
    assert stub_adapters == []


def test_reopening_relaunches_only_what_was_closed(ctx, backend, stub_adapters):
    """A context with one window closed by hand gets that one window back.

    Skipping any screen that held a window meant an app closed on its own never
    returned: the workspace was not empty, so nothing relaunched.
    """
    backend.place_windows("ctx-work", "a.desktop")
    result = launch_context(ctx, backend=backend)
    assert not result.reused_workspace
    assert stub_adapters == ["b.desktop"]


def test_a_window_whose_class_drops_the_desktop_suffix_still_counts(
    ctx, backend, stub_adapters
):
    """Window classes rarely match desktop ids: `element`, not `element.desktop`."""
    backend.place_windows("ctx-work", "a", "b")
    launch_context(ctx, backend=backend)
    assert stub_adapters == []


def test_reopening_a_closed_context_relaunches(ctx, backend, stub_adapters):
    """A closed context keeps its handle, so its workspace exists but is empty.

    Treating "workspace exists" as "already running" left the context empty —
    the bug that made close-then-reopen do nothing.
    """
    backend.workspaces["ctx-work"] = 0
    result = launch_context(ctx, backend=backend)
    assert not result.reused_workspace
    assert stub_adapters == ["a.desktop", "b.desktop"]


def test_failed_launch_does_not_stop_the_rest(ctx, backend, monkeypatch):
    launched: list[str] = []

    class PartlyBroken:
        def launch(self, resource, context_id):
            if resource.app_id == "a.desktop":
                raise LookupError("no desktop entry")
            launched.append(resource.app_id)

    monkeypatch.setattr(launcher.adapters, "adapter_for", lambda r: PartlyBroken())
    result = launch_context(ctx, backend=backend)

    assert launched == ["b.desktop"]
    assert [app for app, _ in result.failed] == ["a.desktop"]
    assert not result.ok


def test_ratios_applied_after_launching(ctx, backend, stub_adapters):
    """preselect only picks a side; proportions are corrected afterwards."""
    launch_context(ctx, backend=backend)
    assert ("ratios", "ctx-work", 2) in backend.calls
    assert [c[0] for c in backend.calls].index("ratios") > [
        c[0] for c in backend.calls
    ].index("preselect")


def test_a_context_with_no_layout_gets_one(backend, stub_adapters):
    """Healing fills in a layout, so every context launches into a known
    arrangement rather than wherever the compositor happens to put things."""
    ctx = Context(title="plain", resources=[Resource(app_id="a.desktop")])
    launch_context(ctx, backend=backend)
    assert len(ctx.layout.slots) == 1
    # One window has nothing to split against, so no ratio pass is needed.
    assert not backend.sequence("ratios")


def test_a_broken_layout_is_repaired_on_launch(backend, stub_adapters):
    """A hand-edited contexts.json should not stop a context opening."""
    from context.layout import Layout, Slot

    ctx = Context(
        title="broken",
        resources=[Resource(app_id="a.desktop"), Resource(app_id="b.desktop")],
        layout=Layout(slots=[Slot(0.0, 0.0, 0.0, 0.0)]),
    )
    result = launch_context(ctx, backend=backend)

    assert result.layout_repaired
    assert len(ctx.layout.slots) == 2
    assert stub_adapters == ["a.desktop", "b.desktop"]


def test_close_keeps_the_definition(ctx, backend, stub_adapters):
    launch_context(ctx, backend=backend)

    result = close_context(ctx, backend=backend)
    assert result.was_open
    # One window per app, which is what launching the context produced.
    assert result.closed == len(ctx.resources)
    # The context itself survives; only its windows are gone.
    assert ctx.title == "work"
    assert ctx.resources


def test_close_drops_the_handle_when_the_workspace_goes(ctx, backend, stub_adapters):
    launch_context(ctx, backend=backend)
    backend.add_window("ctx-work", 1)
    close_context(ctx, backend=backend)
    assert ctx.handle_for("fake") is None


def test_closing_something_not_open_is_harmless(ctx, backend):
    result = close_context(ctx, backend=backend)
    assert not result.was_open
    assert result.closed == 0


def test_context_is_open_requires_windows(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    backend.workspaces["ctx-work"] = 0
    assert not context_is_open(ctx, backend=backend)
    backend.add_window("ctx-work", 1)
    assert context_is_open(ctx, backend=backend)


def test_rename_keeps_the_same_workspace(ctx, backend, stub_adapters):
    """Identity is the handle, not the title.

    Matching on the title orphaned the old workspace and made a second one.
    """
    launch_context(ctx, backend=backend)
    ctx.title = "renamed"
    result = launch_context(ctx, backend=backend)
    assert result.workspace == "ctx-work"


def test_reconnect_adopts_running_contexts(backend):
    """A restart must not offer to relaunch what is already open."""
    live = Context(title="live")
    live.set_handle("fake", "ctx-live")
    backend.workspaces["ctx-live"] = 2

    assert launcher.reconnect([live], backend=backend) == [live]
    assert live.handle_for("fake") == "ctx-live"


def test_reconnect_drops_stale_handles(backend):
    """A context whose windows are gone must rebuild, not reuse an empty one."""
    stale = Context(title="stale")
    stale.set_handle("fake", "ctx-stale")
    backend.workspaces["ctx-stale"] = 0

    assert launcher.reconnect([stale], backend=backend) == []
    assert stale.handle_for("fake") is None


def test_reconnect_ignores_contexts_never_launched(backend):
    never = Context(title="never")
    assert launcher.reconnect([never], backend=backend) == []


def test_open_state_answers_for_every_context_at_once(backend):
    """One query for the whole list, not two per context.

    The launcher polls this every couple of seconds. Asking per context spawned
    two subprocesses each, which is enough work on the main loop to be felt.
    """
    open_ctx = Context(title="open", resources=[])
    shut_ctx = Context(title="shut", resources=[])
    never = Context(title="never", resources=[])

    open_ctx.set_handle("fake", "ctx-open")
    shut_ctx.set_handle("fake", "ctx-shut")

    backend.workspaces = {"ctx-open": 2, "ctx-shut": 0}
    backend.current = "ctx-open"

    open_ids, active_id = launcher.open_state(
        [open_ctx, shut_ctx, never], backend=backend
    )

    assert open_ids == {open_ctx.id}
    assert active_id == open_ctx.id
    assert len(backend.sequence("live")) == 1


def test_open_state_ignores_contexts_with_no_handle(backend):
    unplaced = Context(title="unplaced", resources=[])
    backend.workspaces = {"ctx-other": 1}

    open_ids, active_id = launcher.open_state([unplaced], backend=backend)

    assert open_ids == set()
    assert active_id is None


def test_open_state_reports_an_empty_workspace_as_closed(backend):
    """A closed context keeps its handle, so the handle alone proves nothing."""
    ctx = Context(title="emptied", resources=[])
    ctx.set_handle("fake", "ctx-emptied")
    backend.workspaces = {"ctx-emptied": 0}

    open_ids, _ = launcher.open_state([ctx], backend=backend)

    assert open_ids == set()


# -- spanning screens --------------------------------------------------------


def _two_screens(backend):
    from context.backends.base import MonitorInfo

    backend.outputs = [
        MonitorInfo(name="eDP-1", width=1920, height=1080, focused=True),
        MonitorInfo(name="HDMI-A-1", width=1920, height=1080, x=1920),
    ]
    return backend


def test_a_context_gets_a_workspace_per_screen(ctx, backend, stub_adapters):
    from context.arrangement import Arrangement

    _two_screens(backend)
    ctx.set_arrangement(2, Arrangement.spread(2, 2))

    result = launch_context(ctx, backend=backend)

    assert result.screens == ["ctx-work", "ctx-work-s2"]
    assert ctx.handles_for("fake") == ["ctx-work", "ctx-work-s2"]


def test_each_screen_gets_its_own_workspace_on_its_own_monitor(
    ctx, backend, stub_adapters
):
    from context.arrangement import Arrangement

    _two_screens(backend)
    ctx.set_arrangement(2, Arrangement.spread(2, 2))
    launch_context(ctx, backend=backend)

    assert backend.placements == {
        "ctx-work": "eDP-1",
        "ctx-work-s2": "HDMI-A-1",
    }


def test_windows_launch_onto_the_screen_they_are_assigned(
    ctx, backend, stub_adapters
):
    from context.arrangement import Arrangement

    _two_screens(backend)
    ctx.set_arrangement(2, Arrangement.spread(2, 2))
    launch_context(ctx, backend=backend)

    # One app per screen, and both launched.
    assert sorted(stub_adapters) == ["a.desktop", "b.desktop"]


def test_one_screen_still_uses_one_workspace(ctx, backend, stub_adapters):
    """Nothing changes for a context that does not span."""
    result = launch_context(ctx, backend=backend)
    assert result.screens == ["ctx-work"]
    assert ctx.handles_for("fake") == ["ctx-work"]


def test_undocking_falls_back_to_the_single_screen_arrangement(
    ctx, backend, stub_adapters
):
    """A context laid out for two monitors still opens on one."""
    from context.arrangement import Arrangement

    ctx.set_arrangement(2, Arrangement.spread(2, 2))
    # Only one output attached now.
    result = launch_context(ctx, backend=backend)

    assert result.screens == ["ctx-work"]
    # Both windows land, rather than one being stranded on a screen that is gone.
    assert sorted(stub_adapters) == ["a.desktop", "b.desktop"]


def test_closing_shuts_every_screen(ctx, backend, stub_adapters):
    from context.arrangement import Arrangement

    _two_screens(backend)
    ctx.set_arrangement(2, Arrangement.spread(2, 2))
    launch_context(ctx, backend=backend)
    backend.add_window("ctx-work")
    backend.add_window("ctx-work-s2")

    result = close_context(ctx, backend=backend)

    assert result.was_open
    closed = [c[1] for c in backend.calls if c[0] == "close"]
    assert closed == ["ctx-work", "ctx-work-s2"]


def test_a_context_is_open_when_any_screen_has_windows(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    backend.workspaces = {"ctx-work": 0, "ctx-work-s2": 2}

    assert context_is_open(ctx, backend=backend)


def test_a_context_is_active_from_any_of_its_screens(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    backend.workspaces = {"ctx-work": 1, "ctx-work-s2": 1}
    backend.current = "ctx-work-s2"

    open_ids, active_id = launcher.open_state([ctx], backend=backend)
    assert open_ids == {ctx.id}
    assert active_id == ctx.id


def test_reconnect_keeps_a_context_whose_second_screen_is_live(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    backend.workspaces = {"ctx-work": 0, "ctx-work-s2": 1}

    assert launcher.reconnect([ctx], backend=backend) == [ctx]
    assert ctx.handles_for("fake") == ["ctx-work", "ctx-work-s2"]


def test_reconnect_drops_every_handle_when_nothing_is_live(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    backend.workspaces = {}

    assert launcher.reconnect([ctx], backend=backend) == []
    assert ctx.handles_for("fake") == []


def test_the_launch_ends_on_the_primary_screen(ctx, backend, stub_adapters):
    """Opening a context should leave you looking at its main work."""
    from context.arrangement import Arrangement

    _two_screens(backend)
    ctx.set_arrangement(2, Arrangement.spread(2, 2))
    launch_context(ctx, backend=backend)

    assert backend.current == "ctx-work"


# -- handing the keyboard back ------------------------------------------------


def test_hand_keyboard_back_focuses_the_most_recent_window(backend):
    """Hyprland does not re-send the keyboard enter when a layer lets go, so
    the window it still counts as focused is focused again explicitly."""
    from context.backends.base import WindowInfo

    backend.open_windows = [
        WindowInfo(id="0xrecent", title="editor", app_id="editor"),
        WindowInfo(id="0xolder", title="terminal", app_id="terminal"),
    ]

    launcher.hand_keyboard_back(backend=backend)

    assert backend.focused == "0xrecent"


def test_hand_keyboard_back_with_nothing_open_does_nothing(backend):
    launcher.hand_keyboard_back(backend=backend)

    assert backend.focused is None


def test_closing_a_picker_hands_the_keyboard_back(backend):
    """Dismissing the switcher without choosing anything left typing dead:
    the overlay's unmap reported the window active without the keyboard."""
    from context.app import ContextApplication
    from context.backends.base import WindowInfo

    app = ContextApplication()
    app.backend = backend
    backend.open_windows = [WindowInfo(id="0xrecent", title="editor", app_id="editor")]

    app._on_switcher_closed(None)

    assert backend.focused == "0xrecent"

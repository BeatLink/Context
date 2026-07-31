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
def stub_adapters(monkeypatch):
    """Record launches instead of starting real applications."""
    launched: list[str] = []

    class StubAdapter:
        def launch(self, resource, context_id):
            launched.append(resource.app_id)

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
    backend.workspaces["ctx-work"] = 2
    result = launch_context(ctx, backend=backend)
    assert result.reused_workspace
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
    backend.add_window("ctx-work", 2)

    result = close_context(ctx, backend=backend)
    assert result.was_open
    assert result.closed == 2
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

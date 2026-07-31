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


def test_no_layout_means_no_ratio_pass(backend, stub_adapters):
    ctx = Context(title="plain", resources=[Resource(app_id="a.desktop")])
    launch_context(ctx, backend=backend)
    assert not backend.sequence("ratios")


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

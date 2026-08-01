"""Managing windows once a context is running.

Moving windows between contexts and screens, adopting the ones that belong to
none, and saving what a context has become.
"""

from __future__ import annotations

import pytest

from context.system import launcher
from context.state.arrangement import Arrangement
from context.system.backends.base import MonitorInfo, WindowInfo
from context.state.resources import Resource
from context.state.store import Context


@pytest.fixture
def ctx():
    return Context(title="work", resources=[Resource(app_id="a"), Resource(app_id="b")])


# -- moving between contexts -------------------------------------------------


def test_a_window_moves_into_a_context(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    backend.add_window("ctx-other")
    window = backend.open_windows[0]

    assert launcher.move_window_to_context(window.id, ctx, backend=backend)
    assert backend.open_windows[0].handle == "ctx-work"


def test_a_window_cannot_move_into_a_context_with_no_workspace(ctx, backend):
    """A named workspace does not exist until something is on it."""
    backend.add_window("ctx-other")
    window = backend.open_windows[0]

    assert not launcher.move_window_to_context(window.id, ctx, backend=backend)


def test_moving_falls_back_to_the_primary_screen(ctx, backend):
    """Asking for a screen the context does not have is not a failure."""
    ctx.set_handle("fake", "ctx-work")
    backend.add_window("ctx-other")
    window = backend.open_windows[0]

    assert launcher.move_window_to_context(window.id, ctx, screen=3, backend=backend)
    assert backend.open_windows[0].handle == "ctx-work"


# -- moving between a context's screens --------------------------------------


def test_a_window_moves_to_the_next_screen(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    backend.add_window("ctx-work")
    window = backend.open_windows[0]

    assert launcher.move_window_to_screen(window.id, ctx, 1, backend=backend)
    assert backend.open_windows[0].handle == "ctx-work-s2"


def test_a_window_does_not_fall_off_the_end(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    backend.add_window("ctx-work-s2")
    window = backend.open_windows[0]

    assert not launcher.move_window_to_screen(window.id, ctx, 1, backend=backend)


def test_a_single_screen_context_has_nowhere_to_throw(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    backend.add_window("ctx-work")
    window = backend.open_windows[0]

    assert not launcher.move_window_to_screen(window.id, ctx, 1, backend=backend)


# -- unmanaged windows -------------------------------------------------------


def test_windows_outside_every_context_are_unmanaged(ctx, backend):
    ctx.set_handle("fake", "ctx-work")
    backend.add_window("ctx-work")
    backend.add_window("random")

    loose = launcher.unmanaged_windows([ctx], backend=backend)
    assert [w.handle for w in loose] == ["random"]


def test_a_context_claims_every_one_of_its_screens(ctx, backend):
    """A window on the second screen is managed, not loose."""
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)
    backend.add_window("ctx-work")
    backend.add_window("ctx-work-s2")

    assert launcher.unmanaged_windows([ctx], backend=backend) == []


def test_nothing_is_unmanaged_when_nothing_is_open(ctx, backend):
    assert launcher.unmanaged_windows([ctx], backend=backend) == []


# -- capturing what a context became -----------------------------------------


class GeometryBackend:
    """A backend that can report where windows actually are."""

    name = "fake"

    def __init__(self, geometry: dict[str, list[dict]]) -> None:
        self.geometry = geometry

    def monitors(self):
        return [
            MonitorInfo(name="A", width=1000, height=1000),
            MonitorInfo(name="B", width=1000, height=1000, x=1000),
        ]

    def client_geometry(self, handle: str) -> list[dict]:
        return self.geometry.get(handle, [])


def test_capture_reads_live_positions_into_the_arrangement(ctx):
    wm = GeometryBackend(
        {
            "ctx-work": [
                {"app_id": "a", "x": 0, "y": 0, "width": 500, "height": 1000},
                {"app_id": "b", "x": 500, "y": 0, "width": 500, "height": 1000},
            ]
        }
    )
    ctx.set_handle("fake", "ctx-work")

    windows, screens = launcher.capture_arrangement(ctx, backend=wm)

    assert (windows, screens) == (2, 1)
    slots = ctx.arrangement_for(1).layout_for(0).slots
    assert slots[0].width == pytest.approx(0.5)
    assert slots[1].x == pytest.approx(0.5)


def test_capture_records_which_screen_each_window_is_on(ctx):
    wm = GeometryBackend(
        {
            "ctx-work": [{"app_id": "a", "x": 0, "y": 0, "width": 1000, "height": 1000}],
            "ctx-work-s2": [
                {"app_id": "b", "x": 1000, "y": 0, "width": 1000, "height": 1000}
            ],
        }
    )
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)

    windows, screens = launcher.capture_arrangement(ctx, backend=wm)

    assert (windows, screens) == (2, 2)
    arrangement = ctx.arrangement_for(2)
    assert arrangement.screen_for(0) == 0
    assert arrangement.screen_for(1) == 1


def test_capture_positions_are_relative_to_the_screen(ctx):
    """A window at x=1000 on the second monitor is at 0.0, not 1.0."""
    wm = GeometryBackend(
        {
            "ctx-work": [],
            "ctx-work-s2": [
                {"app_id": "b", "x": 1000, "y": 0, "width": 500, "height": 1000}
            ],
        }
    )
    ctx.set_handle("fake", "ctx-work")
    ctx.set_handle("fake", "ctx-work-s2", screen=1)

    launcher.capture_arrangement(ctx, backend=wm)

    slot = ctx.arrangement_for(2).layout_for(1).slots[0]
    assert slot.x == pytest.approx(0.0)


def test_capture_keeps_what_an_app_opens(ctx):
    """Capturing positions must not throw away a resource's URLs."""
    ctx.resources = [Resource(app_id="a", urls=["https://example.com"])]
    wm = GeometryBackend(
        {"ctx-work": [{"app_id": "a", "x": 0, "y": 0, "width": 1000, "height": 1000}]}
    )
    ctx.set_handle("fake", "ctx-work")

    launcher.capture_arrangement(ctx, backend=wm)

    assert ctx.resources[0].urls == ["https://example.com"]


def test_capture_of_an_empty_context_changes_nothing(ctx):
    wm = GeometryBackend({"ctx-work": []})
    ctx.set_handle("fake", "ctx-work")
    before = list(ctx.resources)

    assert launcher.capture_arrangement(ctx, backend=wm) == (0, 0)
    assert ctx.resources == before


def test_capture_without_a_workspace_does_nothing(ctx, backend):
    assert launcher.capture_arrangement(ctx, backend=backend) == (0, 0)


# -- screen order ------------------------------------------------------------


def test_screens_are_ordered_by_position_not_focus(backend):
    """Screen 1 must be the same physical monitor every time.

    Sorting by focus would mean an app the user put on their right-hand
    display opened on the left whenever they launched from there.
    """
    backend.outputs = [
        MonitorInfo(name="right", width=1920, height=1080, x=1920, focused=True),
        MonitorInfo(name="left", width=1920, height=1080, x=0),
    ]
    assert [m.name for m in launcher._outputs(backend)] == ["left", "right"]


def test_a_windows_assigned_screen_is_honoured_whatever_is_focused(
    ctx, backend, monkeypatch
):
    from context.system import adapters

    launched: list[tuple[str, str]] = []

    class StubAdapter:
        def launch(self, resource, context_id):
            launched.append((resource.app_id, backend.current))
            if backend.current:
                backend.add_window(backend.current)

    monkeypatch.setattr(adapters, "adapter_for", lambda r: StubAdapter())
    backend.outputs = [
        MonitorInfo(name="left", width=1920, height=1080, x=0),
        MonitorInfo(name="right", width=1920, height=1080, x=1920, focused=True),
    ]
    # b is pinned to screen 2 regardless of where focus is.
    ctx.set_arrangement(2, Arrangement(screens=[None, None], assignments={0: 0, 1: 1}))
    ctx.arrangements[2].screens = [
        ctx.arrangement_for(1).layout_for(0),
        ctx.arrangement_for(1).layout_for(0),
    ]

    launcher.launch_context(ctx, backend=backend)

    placed = dict(launched)
    assert placed["a"] == "ctx-work"
    assert placed["b"] == "ctx-work-s2"
    assert backend.placements["ctx-work-s2"] == "right"


def test_capture_is_relative_to_the_windows_wherever_they_are():
    """Which monitor they are on cannot come into it.

    Slots are fractions of the area the windows span, so a context on the
    second monitor captures the same numbers as the same arrangement on the
    first — 1200px into the desktop is the left-hand window either way.
    """
    ctx = Context(title="work")
    ctx.set_handle("fake", "ctx-work")

    wm = GeometryBackend(
        {
            "ctx-work": [
                {"app_id": "a", "x": 1200, "y": 0, "width": 400, "height": 1000},
                {"app_id": "b", "x": 1600, "y": 0, "width": 400, "height": 1000},
            ]
        }
    )

    launcher.capture_arrangement(ctx, backend=wm)

    slots = ctx.arrangement_for(1).layout_for(0).slots
    assert slots[0].x == pytest.approx(0.0)
    assert slots[1].x == pytest.approx(0.5)
    assert slots[0].width == pytest.approx(0.5)


# -- drift -------------------------------------------------------------------


def test_a_context_that_matches_its_layout_has_not_drifted(ctx):
    wm = GeometryBackend(
        {"ctx-work": [{"app_id": "a", "x": 0, "y": 0, "width": 1000, "height": 1000}]}
    )
    ctx.set_handle("fake", "ctx-work")
    launcher.capture_arrangement(ctx, backend=wm)

    assert not launcher.has_drifted(ctx, backend=wm)


def test_a_resized_split_counts_as_drift(ctx):
    wm = GeometryBackend(
        {
            "ctx-work": [
                {"app_id": "a", "x": 0, "y": 0, "width": 500, "height": 1000},
                {"app_id": "b", "x": 500, "y": 0, "width": 500, "height": 1000},
            ]
        }
    )
    ctx.set_handle("fake", "ctx-work")
    launcher.capture_arrangement(ctx, backend=wm)

    wm.geometry["ctx-work"] = [
        {"app_id": "a", "x": 0, "y": 0, "width": 700, "height": 1000},
        {"app_id": "b", "x": 700, "y": 0, "width": 300, "height": 1000},
    ]
    assert launcher.has_drifted(ctx, backend=wm)


def test_one_tiled_window_cannot_drift_where_it_sits(ctx):
    """Slots are fractions of the area the windows span, and a lone window is
    that whole area — wherever the compositor put it. Which is the point: the
    bars, the sidebar and the gaps all move it about without the context
    having changed at all."""
    wm = GeometryBackend(
        {"ctx-work": [{"app_id": "a", "x": 0, "y": 0, "width": 1000, "height": 1000}]}
    )
    ctx.set_handle("fake", "ctx-work")
    launcher.capture_arrangement(ctx, backend=wm)

    wm.geometry["ctx-work"] = [
        {"app_id": "a", "x": 60, "y": 48, "width": 900, "height": 900}
    ]
    assert not launcher.has_drifted(ctx, backend=wm)


def test_a_new_window_counts_as_drift(ctx):
    wm = GeometryBackend(
        {"ctx-work": [{"app_id": "a", "x": 0, "y": 0, "width": 1000, "height": 1000}]}
    )
    ctx.set_handle("fake", "ctx-work")
    launcher.capture_arrangement(ctx, backend=wm)

    wm.geometry["ctx-work"].append(
        {"app_id": "b", "x": 0, "y": 0, "width": 500, "height": 500}
    )
    assert launcher.has_drifted(ctx, backend=wm)


def test_a_few_pixels_is_not_drift(ctx):
    """Gaps and borders never divide evenly into a fraction of the screen.

    Treating that as a change would make the offer to save worthless, since it
    would appear for a context nobody had touched.
    """
    wm = GeometryBackend(
        {"ctx-work": [{"app_id": "a", "x": 0, "y": 0, "width": 1000, "height": 1000}]}
    )
    ctx.set_handle("fake", "ctx-work")
    launcher.capture_arrangement(ctx, backend=wm)

    wm.geometry["ctx-work"] = [
        {"app_id": "a", "x": 4, "y": 4, "width": 992, "height": 992}
    ]
    assert not launcher.has_drifted(ctx, backend=wm)


def test_a_context_with_no_workspace_has_not_drifted(ctx, backend):
    assert not launcher.has_drifted(ctx, backend=backend)


def test_the_no_context_is_everything_no_context_claims(backend):
    """One query answers open, focused, drifted and homeless together."""
    from context.system import launcher
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("work")
    ctx.set_handle("fake", "ctx-work")
    backend.workspaces["ctx-work"] = 1
    backend.geometry = {
        "ctx-work": [
            {"id": "0x1", "app_id": "a", "x": 0, "y": 0, "width": 1920, "height": 1080}
        ],
        "3": [
            {"id": "0x2", "app_id": "b", "x": 0, "y": 0, "width": 960, "height": 1080},
            {"id": "0x3", "app_id": "c", "x": 960, "y": 0, "width": 960, "height": 1080},
        ],
    }

    live = launcher.read_live_state(store.contexts, backend=backend)
    assert live.open_ids == {ctx.id}
    assert [w["id"] for w in live.loose] == ["0x2", "0x3"]

    loose = launcher.loose_context(live.loose)
    assert launcher.is_no_context(loose)
    assert [r.app_id for r in loose.resources] == ["b", "c"]
    # Nothing homeless, nothing to stand in for.
    assert launcher.loose_context([]) is None


def test_closing_the_no_context_closes_exactly_those_windows(backend):
    from context.system import launcher

    loose = [{"id": "0x2", "app_id": "b"}, {"id": "0x3", "app_id": "c"}]
    assert launcher.close_loose(loose, backend=backend) == 2
    assert backend.closed_windows == ["0x2", "0x3"]


def test_saving_the_no_context_gathers_its_windows_in(backend):
    """The windows are scattered across whatever workspaces they opened on, so
    saving them means moving them somewhere their positions mean something."""
    from context.system import launcher
    from context.state.store import ContextStore

    store = ContextStore()
    backend.place_windows("3", "b.desktop")
    backend.place_windows("7", "c.desktop")
    loose = [
        {"id": w.id, "app_id": w.app_id} for w in backend.open_windows
    ]
    ctx = store.create("gathered")

    moved = launcher.adopt_loose(ctx, loose, backend=backend)
    assert moved == 2
    handle = ctx.handle_for("fake")
    assert handle is not None
    assert {w.handle for w in backend.open_windows} == {handle}
    # And it is the workspace you are looking at, since that is where the
    # compositor tiles them.
    assert backend.current == handle


def test_the_context_you_are_in_is_open_even_with_no_windows(backend):
    """A context with no apps, or one whose windows you have just closed while
    standing in it, was listed as not running while you looked straight at it."""
    from context.system import launcher
    from context.state.store import ContextStore

    store = ContextStore()
    empty = store.create("blank")
    empty.set_handle("fake", "ctx-blank")
    backend.current = "ctx-blank"

    open_ids, active_id = launcher.open_state(store.contexts, backend=backend)
    assert open_ids == {empty.id}
    assert active_id == empty.id

    # Switch away and it is not running any more: there is nothing to go back to.
    backend.current = None
    assert launcher.open_state(store.contexts, backend=backend)[0] == set()

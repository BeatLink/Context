"""The order contexts were visited, which is what alt-tab needs.

Kept apart from the contexts themselves: it is the order the user moved through
them, not a property of any one context, and it must not rewrite contexts.json
on every switch.
"""

from __future__ import annotations

import pytest

from context.state import uistate


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    yield tmp_path


def test_the_most_recent_visit_comes_first():
    uistate.note_visit("a")
    uistate.note_visit("b")
    assert uistate.get(uistate.RECENT_KEY) == ["b", "a"]


def test_revisiting_moves_it_up_rather_than_duplicating():
    for context_id in ("a", "b", "c", "a"):
        uistate.note_visit(context_id)
    assert uistate.get(uistate.RECENT_KEY) == ["a", "c", "b"]


def test_previous_skips_the_one_you_are_in():
    """Alt-tab goes back, not nowhere."""
    uistate.note_visit("a")
    uistate.note_visit("b")
    assert uistate.previous_context("b") == "a"
    assert uistate.previous_context("a") == "b"


def test_previous_is_none_with_nothing_to_go_back_to():
    assert uistate.previous_context(None) is None
    uistate.note_visit("a")
    assert uistate.previous_context("a") is None


def test_the_history_is_bounded():
    for index in range(uistate.RECENT_LIMIT + 10):
        uistate.note_visit(f"ctx-{index}")
    assert len(uistate.get(uistate.RECENT_KEY)) == uistate.RECENT_LIMIT


def test_a_corrupt_history_is_ignored():
    uistate.save(**{uistate.RECENT_KEY: ["a", 5, None, "b"]})
    uistate.note_visit("c")
    assert uistate.get(uistate.RECENT_KEY) == ["c", "a", "b"]


def test_how_long_ago_reads_the_way_a_person_says_it():
    from context.system.apps import DAY, HOUR, recency_heading

    assert recency_heading(0) == "Just now"
    assert recency_heading(59 * 60) == "Just now"
    assert recency_heading(HOUR) == "1 hour ago"
    assert recency_heading(5 * HOUR) == "5 hours ago"
    assert recency_heading(DAY) == "1 day ago"
    assert recency_heading(3 * DAY) == "3 days ago"
    assert recency_heading(8 * DAY) == "1 week ago"
    assert recency_heading(21 * DAY) == "3 weeks ago"
    assert recency_heading(200 * DAY) == "6 months ago"


def test_launching_an_app_is_what_makes_it_recent(tmp_path, monkeypatch):
    """Recorded on the launch, so an app started as part of a context counts
    the same as one picked out of the grid."""
    import time

    from context.state import uistate
    from context.system.apps import App, arrange_apps

    monkeypatch.setenv("CONTEXT_UI_STATE", str(tmp_path / "ui.json"))
    now = time.time()
    uistate.note_app("b.desktop", when=now - 10)
    uistate.note_app("a.desktop", when=now - 2 * 86400)

    assert uistate.recent_apps() == ["b.desktop", "a.desktop"]

    apps = [
        App(id="a.desktop", name="Alpha", description="", icon=None),
        App(id="b.desktop", name="Beta", description="", icon=None),
        App(id="c.desktop", name="Gamma", description="", icon=None),
    ]
    sections = arrange_apps(apps, "recent", times=uistate.app_times(), now=now)
    assert [(head, [a.name for a in found]) for head, found in sections] == [
        ("Just now", ["Beta"]),
        ("2 days ago", ["Alpha"]),
        ("Not opened yet", ["Gamma"]),
    ]


def test_an_order_without_times_is_not_guessed_at(tmp_path, monkeypatch):
    """The first shape this took was a plain list. Nothing can be said about
    when those launches happened, so they are dropped rather than invented."""
    import json

    from context.state import uistate

    path = tmp_path / "ui.json"
    path.write_text(json.dumps({"recent_apps": ["a.desktop", "b.desktop"]}))
    monkeypatch.setenv("CONTEXT_UI_STATE", str(path))

    assert uistate.app_times() == {}
    assert uistate.recent_apps() == []

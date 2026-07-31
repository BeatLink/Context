"""The order contexts were visited, which is what alt-tab needs.

Kept apart from the contexts themselves: it is the order the user moved through
them, not a property of any one context, and it must not rewrite contexts.json
on every switch.
"""

from __future__ import annotations

import pytest

from context import uistate


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

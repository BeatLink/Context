"""The scratchpad: one note for the desk, one per context, saved as you type.

There is nothing to create, nothing to name and nothing to choose between, so
most of what is worth pinning is the formatting and the saving.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import needs_display, run_app

from context.state import scratchpad
from context.state.scratchpad import GLOBAL, Note, NoteStore


# -- one note per place ------------------------------------------------------


def test_a_context_and_the_desk_have_separate_scratchpads(tmp_path):
    store = NoteStore(path=tmp_path / "s.json")
    store.set_body(GLOBAL, "everywhere")
    store.set_body("ctx-1", "just here")

    assert store.body(GLOBAL) == "everywhere"
    assert store.body("ctx-1") == "just here"


def test_a_context_never_typed_in_still_has_a_scratchpad(tmp_path):
    """Nothing is created, so there is no state where a context has no note."""
    store = NoteStore(path=tmp_path / "s.json")
    note = store.get("brand-new")
    assert isinstance(note, Note)
    assert note.body == ""
    assert note.is_empty


def test_the_global_note_is_the_one_with_no_context(tmp_path):
    store = NoteStore(path=tmp_path / "s.json")
    assert store.get(GLOBAL).is_global
    assert not store.get("ctx-1").is_global


def test_forgetting_a_context_drops_its_scratchpad(tmp_path):
    path = tmp_path / "s.json"
    store = NoteStore(path=path)
    store.set_body("ctx-1", "notes about a thing")
    store.forget("ctx-1")
    assert NoteStore(path=path).body("ctx-1") == ""


# -- saving ------------------------------------------------------------------


def test_writing_a_body_persists_it(tmp_path):
    path = tmp_path / "s.json"
    NoteStore(path=path).set_body("ctx-1", "- [ ] milk")
    assert NoteStore(path=path).body("ctx-1") == "- [ ] milk"


def test_writing_the_same_text_again_does_not_touch_the_note(tmp_path):
    """The autosave timer can fire on an unchanged buffer, and rewriting the
    file every time it does is how a scratchpad churns the disk."""
    store = NoteStore(path=tmp_path / "s.json")
    note = store.set_body("ctx-1", "same")
    when = note.updated_at
    again = store.set_body("ctx-1", "same")
    assert again.updated_at == when


def test_an_empty_scratchpad_is_not_written_to_the_file(tmp_path):
    path = tmp_path / "s.json"
    store = NoteStore(path=path)
    store.set_body("ctx-1", "something")
    store.set_body("ctx-2", "")
    stored = json.loads(path.read_text())
    assert set(stored["notes"]) == {"ctx-1"}


def test_clearing_empties_the_note(tmp_path):
    path = tmp_path / "s.json"
    store = NoteStore(path=path)
    store.set_body("ctx-1", "gone in a moment")
    store.clear("ctx-1")
    assert NoteStore(path=path).body("ctx-1") == ""


def test_an_unreadable_file_does_not_stop_the_store_loading(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{ not json")
    assert NoteStore(path=path).notes == {}


def test_a_file_of_the_wrong_shape_is_ignored(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"notes": "not an object"}))
    assert NoteStore(path=path).notes == {}


def test_the_versioned_format_is_not_read(tmp_path):
    """A hard cutover: the shape with titles and histories is left behind
    rather than guessed at."""
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "notes": [
                    {
                        "id": "x",
                        "title": "Test",
                        "context_id": "ctx-1",
                        "versions": [{"number": 1, "body": "old"}],
                    }
                ],
            }
        )
    )
    assert NoteStore(path=path).notes == {}


# -- which scratchpads exist --------------------------------------------------


def test_global_is_always_the_first_button(tmp_path, monkeypatch):
    """It is the one that is always there, so it holds the same place whether or
    not you are in a context — the buttons must not shuffle as you move."""
    from context.state import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    store = NoteStore(path=tmp_path / "s.json")

    assert store.available("ctx-1") == [GLOBAL, "ctx-1"]
    assert store.available(None) == [GLOBAL]


def test_where_you_are_is_still_what_opens(tmp_path, monkeypatch):
    """Order and default are separate: Global sits first, the context's opens."""
    from context.state import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    store = NoteStore(path=tmp_path / "s.json")

    assert store.preferred("ctx-1") == "ctx-1"
    assert store.preferred(None) == GLOBAL

    settings.update(scratchpad_per_context=False)
    assert store.preferred("ctx-1") == GLOBAL


def test_the_settings_decide_which_scratchpads_exist(tmp_path, monkeypatch):
    from context.state import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "_current", None)
    store = NoteStore(path=tmp_path / "s.json")

    settings.update(scratchpad_global=False)
    assert store.available("ctx-1") == ["ctx-1"]

    settings.update(scratchpad_global=True, scratchpad_per_context=False)
    assert store.available("ctx-1") == [GLOBAL]

    settings.update(scratchpad=False)
    assert store.available("ctx-1") == []


# -- formatting --------------------------------------------------------------


@pytest.mark.parametrize(
    "line, kind, text",
    [
        ("- milk", scratchpad.BULLET, "milk"),
        ("* milk", scratchpad.BULLET, "milk"),
        ("- [ ] milk", scratchpad.UNCHECKED, "milk"),
        ("- [x] milk", scratchpad.CHECKED, "milk"),
        ("- [X] milk", scratchpad.CHECKED, "milk"),
        ("just prose", scratchpad.TEXT, "just prose"),
        ("-not a bullet", scratchpad.TEXT, "-not a bullet"),
    ],
)
def test_lines_parse_to_what_they_look_like(line, kind, text):
    parsed = scratchpad.parse(line)[0]
    assert parsed.kind == kind
    assert parsed.text == text


def test_a_dash_without_a_space_stays_prose():
    assert scratchpad.parse("-5 degrees")[0].kind == scratchpad.TEXT


def test_nesting_is_read_from_the_indent():
    assert [l.indent for l in scratchpad.parse("- top\n  - under\n    - deeper")] == [
        0,
        1,
        2,
    ]


def test_rendering_a_parsed_body_gives_it_back():
    body = "- [ ] milk\n- [x] bread\n  - wholemeal\nplain"
    assert scratchpad.render(scratchpad.parse(body)) == body


def test_toggling_flips_one_checkbox_and_leaves_the_rest():
    body = "- [ ] milk\n- [ ] bread"
    assert scratchpad.toggle(body, 0) == "- [x] milk\n- [ ] bread"
    assert scratchpad.toggle(body, 1) == "- [ ] milk\n- [x] bread"


def test_toggling_something_that_is_not_a_checkbox_changes_nothing():
    body = "- milk\nplain"
    assert scratchpad.toggle(body, 0) == body
    assert scratchpad.toggle(body, 99) == body


def test_changing_a_line_kind_keeps_its_words():
    assert scratchpad.set_kind("- milk", 0, scratchpad.UNCHECKED) == "- [ ] milk"
    assert scratchpad.set_kind("- milk", 0, scratchpad.TEXT) == "milk"


def test_a_list_item_carries_its_marker_to_the_next_line():
    assert scratchpad.continuation("- [ ] milk") == "- [ ] "
    assert scratchpad.continuation("- milk") == "- "
    assert scratchpad.continuation("  - nested") == "  - "


def test_an_empty_item_ends_the_list():
    assert scratchpad.continuation("- ") == ""
    assert scratchpad.continuation("- [ ] ") == ""
    assert scratchpad.continuation("prose") == ""


def test_progress_counts_only_checkboxes():
    assert scratchpad.progress("- [x] a\n- [ ] b\n- c\nd") == (1, 2)
    assert scratchpad.progress("no boxes here") == (0, 0)


def test_the_summary_is_the_first_line_with_anything_on_it():
    assert scratchpad.summary("\n\n- [ ] first real line") == "first real line"
    assert scratchpad.summary("") == ""


# -- the widget --------------------------------------------------------------


@needs_display
def test_typing_into_the_sidebar_saves_without_being_asked(gtk_app, isolated_store):
    """The whole point of it being in the sidebar."""
    from context.ui.scratchpad import ScratchpadView

    seen = {}

    def body(app):
        store = NoteStore()
        view = ScratchpadView(store, context_id="ctx-1")
        view.buffer.set_text("- [ ] milk")
        # The timer would fire on its own; flushing is what leaving does.
        view.flush()
        seen["stored"] = store.body("ctx-1")
        app.quit()

    run_app(gtk_app, body)
    assert seen["stored"] == "- [ ] milk"


@needs_display
def test_the_sidebar_scratchpad_opens_on_the_context_you_are_in(
    gtk_app, isolated_store
):
    from context.ui.scratchpad import ScratchpadView

    seen = {}

    def body(app):
        store = NoteStore()
        store.set_body(GLOBAL, "desk")
        store.set_body("ctx-1", "this job")
        view = ScratchpadView(store, context_id="ctx-1")
        seen["showing"] = view.showing
        seen["text"] = view.body
        app.quit()

    run_app(gtk_app, body)
    assert seen["showing"] == "ctx-1"
    assert seen["text"] == "this job"


@needs_display
def test_switching_to_the_global_pad_saves_the_one_being_left(
    gtk_app, isolated_store
):
    """Switching is the one move that could quietly lose what was just typed."""
    from context.ui.scratchpad import ScratchpadView

    seen = {}

    def body(app):
        store = NoteStore()
        view = ScratchpadView(store, context_id="ctx-1")
        view.buffer.set_text("typed but not saved")
        # Global is the first button; the pad opened on the context's.
        view._on_choice(view.offered.index(GLOBAL))
        seen["left_behind"] = store.body("ctx-1")
        seen["now_showing"] = view.showing
        app.quit()

    run_app(gtk_app, body)
    assert seen["left_behind"] == "typed but not saved"
    assert seen["now_showing"] == GLOBAL


@needs_display
def test_a_refresh_does_not_eat_what_is_being_typed(gtk_app, isolated_store):
    """The sidebar refreshes on a poll timer. Reloading the buffer under the
    cursor would take the word in progress with it."""
    from context.ui.scratchpad import ScratchpadView

    seen = {}

    def body(app):
        store = NoteStore()
        store.set_body("ctx-1", "saved text")
        view = ScratchpadView(store, context_id="ctx-1")
        view.buffer.set_text("half a thoug")
        # A save is pending, which is what "being typed" looks like.
        view.refresh()
        seen["text"] = view.body
        app.quit()

    run_app(gtk_app, body)
    assert seen["text"] == "half a thoug"


@needs_display
def test_the_sidebar_shows_the_scratchpad_rather_than_a_list(gtk_app, isolated_store):
    from context.state.store import ContextStore
    from context.ui.scratchpad import ScratchpadSection
    from context.ui.window import LauncherWindow

    seen = {}

    def body(app):
        notes = NoteStore()
        window = LauncherWindow(
            app, ContextStore(), lambda c: None, lambda c: None, notes=notes
        )
        window.refresh()
        seen["is_a_pad"] = isinstance(window.scratchpad_view, ScratchpadSection)
        seen["visible"] = window.scratchpad_box.get_visible()
        app.quit()

    run_app(gtk_app, body)
    assert seen["is_a_pad"] is True
    assert seen["visible"] is True


@needs_display
def test_the_sidebar_hides_the_scratchpad_when_it_is_switched_off(
    gtk_app, isolated_store
):
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    seen = {}

    def body(app):
        window = LauncherWindow(
            app, ContextStore(), lambda c: None, lambda c: None, notes=NoteStore()
        )
        window.refresh()
        seen["on"] = window.scratchpad_box.get_visible()
        settings.update(scratchpad=False)
        window.refresh()
        seen["off"] = window.scratchpad_box.get_visible()
        app.quit()

    run_app(gtk_app, body)
    assert seen["on"] is True
    assert seen["off"] is False


@needs_display
def test_ticking_a_box_in_the_editor_rewrites_the_body(gtk_app, isolated_store):
    from gi.repository import Gtk

    from context.ui.note_editor import NoteEditorPage

    seen = {}

    def body(app):
        store = NoteStore()
        store.set_body(GLOBAL, "- [ ] milk\n- [ ] bread")
        page = NoteEditorPage(store, on_done=lambda: None)
        page._rebuild_checklist()

        rendered = page.checklist_box.get_first_child()
        boxes = []
        child = rendered.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.CheckButton):
                boxes.append(child)
            child = child.get_next_sibling()
        seen["count"] = len(boxes)
        boxes[0].set_active(True)
        seen["body"] = page.pad.body
        app.quit()

    run_app(gtk_app, body)
    assert seen["count"] == 2
    assert seen["body"] == "- [x] milk\n- [ ] bread"


@needs_display
def test_the_editor_writes_what_the_sidebar_reads(gtk_app, isolated_store):
    """One note, two views of it — there is no copy that can be behind."""
    from context.ui.note_editor import NoteEditorPage
    from context.ui.scratchpad import ScratchpadView

    seen = {}

    def body(app):
        store = NoteStore()
        page = NoteEditorPage(store, on_done=lambda: None)
        page.pad.buffer.set_text("written in the editor")
        page._finish()

        pad = ScratchpadView(store, context_id=None)
        seen["sidebar"] = pad.body
        app.quit()

    run_app(gtk_app, body)
    assert seen["sidebar"] == "written in the editor"


# -- feedback, both at once, and height --------------------------------------


@needs_display
def test_a_pad_says_when_it_has_been_written(gtk_app, isolated_store):
    """An autosave with no sign of having happened asks to be trusted before it
    has earned it."""
    from context.ui.scratchpad import ScratchpadView

    seen = {}

    def body(app):
        view = ScratchpadView(NoteStore(), context_id="ctx-1")
        seen["at_rest"] = view.status.get_label()
        view.buffer.set_text("something")
        seen["while_typing"] = view.status.get_label()
        view.flush()
        seen["after_saving"] = view.status.get_label()
        app.quit()

    run_app(gtk_app, body)
    assert seen["at_rest"] == ""
    assert seen["while_typing"] == "Saving…"
    assert seen["after_saving"] == "Saved"


@needs_display
def test_nothing_is_claimed_to_be_saved_when_nothing_changed(gtk_app, isolated_store):
    from context.ui.scratchpad import ScratchpadView

    seen = {}

    def body(app):
        store = NoteStore()
        store.set_body("ctx-1", "already written")
        view = ScratchpadView(store, context_id="ctx-1")
        view.flush()
        seen["status"] = view.status.get_label()
        app.quit()

    run_app(gtk_app, body)
    assert seen["status"] == ""


@needs_display
def test_showing_both_gives_a_pad_each_and_no_switch(gtk_app, isolated_store):
    from context.state import settings
    from context.ui.scratchpad import ScratchpadSection

    seen = {}

    def body(app):
        store = NoteStore()
        settings.update(scratchpad_show_both=True)
        section = ScratchpadSection(store, context_id="ctx-1")
        seen["pads"] = len(section.views)
        seen["keys"] = [v.showing for v in section.views]
        seen["switches"] = [v.choice for v in section.views]
        app.quit()

    run_app(gtk_app, body)
    assert seen["pads"] == 2
    assert seen["keys"] == [GLOBAL, "ctx-1"]
    assert seen["switches"] == [None, None]


@needs_display
def test_one_at_a_time_is_a_single_pad_with_a_switch(gtk_app, isolated_store):
    from context.ui.scratchpad import ScratchpadSection

    seen = {}

    def body(app):
        section = ScratchpadSection(NoteStore(), context_id="ctx-1")
        seen["pads"] = len(section.views)
        seen["has_switch"] = section.views[0].choice is not None
        app.quit()

    run_app(gtk_app, body)
    assert seen["pads"] == 1
    assert seen["has_switch"] is True


@needs_display
def test_both_pads_write_to_their_own_note(gtk_app, isolated_store):
    from context.state import settings
    from context.ui.scratchpad import ScratchpadSection

    seen = {}

    def body(app):
        store = NoteStore()
        settings.update(scratchpad_show_both=True)
        section = ScratchpadSection(store, context_id="ctx-1")
        section.views[0].buffer.set_text("for the desk")
        section.views[1].buffer.set_text("for this job")
        section.flush()
        seen["global"] = store.body(GLOBAL)
        seen["context"] = store.body("ctx-1")
        app.quit()

    run_app(gtk_app, body)
    assert seen["global"] == "for the desk"
    assert seen["context"] == "for this job"


@needs_display
def test_the_section_rebuilds_when_the_setting_changes(gtk_app, isolated_store):
    """The sidebar reuses its section on every poll, so `matches` is what stops
    a stale arrangement surviving a settings change."""
    from context.state import settings
    from context.ui.scratchpad import ScratchpadSection

    seen = {}

    def body(app):
        section = ScratchpadSection(NoteStore(), context_id="ctx-1")
        seen["before"] = section.matches("ctx-1")
        settings.update(scratchpad_show_both=True)
        seen["after"] = section.matches("ctx-1")
        seen["other_context"] = section.matches("ctx-2")
        app.quit()

    run_app(gtk_app, body)
    assert seen["before"] is True
    assert seen["after"] is False
    assert seen["other_context"] is False


@needs_display
def test_the_writing_area_takes_its_height_from_the_setting(gtk_app, isolated_store):
    from context.state import settings
    from context.ui.scratchpad import ScratchpadView

    seen = {}

    def body(app):
        settings.update(scratchpad_height=260)
        view = ScratchpadView(NoteStore(), context_id="ctx-1", compact=True)
        seen["height"] = view.scroller.get_size_request().height
        app.quit()

    run_app(gtk_app, body)
    assert seen["height"] == 260


def test_the_height_is_clamped_to_what_can_be_rendered():
    from context.state.settings import Settings, MAX_SCRATCHPAD_HEIGHT
    from context.state.settings import MIN_SCRATCHPAD_HEIGHT

    assert Settings(scratchpad_height=1).validated().scratchpad_height == (
        MIN_SCRATCHPAD_HEIGHT
    )
    assert Settings(scratchpad_height=99999).validated().scratchpad_height == (
        MAX_SCRATCHPAD_HEIGHT
    )

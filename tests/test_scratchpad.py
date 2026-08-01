"""The scratchpad: the append-only history, the formatting, and both views.

The history tests are the ones that matter. "Editing an old version does not
truncate the newer ones" is the whole design, and it is exactly the behaviour a
future refactor towards an ordinary undo stack would quietly break.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import needs_display, run_app

from context.state import scratchpad
from context.state.scratchpad import GLOBAL, Note, NoteStore


# -- history -----------------------------------------------------------------


def test_every_edit_appends_rather_than_replacing():
    note = Note(title="Shopping")
    note.revise("one")
    note.revise("two")
    note.revise("three")

    assert [v.number for v in note.versions] == [1, 2, 3]
    assert [v.body for v in note.versions] == ["one", "two", "three"]
    assert note.body == "three"


def test_editing_from_history_keeps_the_versions_after_it():
    """The design in one test: writing from v1 while v3 exists appends v4 and
    leaves v2 and v3 exactly where they were."""
    note = Note()
    note.revise("one")
    note.revise("two")
    note.revise("three")

    note.revise("one and a half", base=1)

    assert [v.number for v in note.versions] == [1, 2, 3, 4]
    assert note.version(2).body == "two"
    assert note.version(3).body == "three"
    assert note.version(4).base == 1
    assert note.body == "one and a half"


def test_the_tip_is_always_the_newest_version_written():
    note = Note()
    note.revise("one")
    note.revise("two")
    note.revise("from the first", base=1)
    assert note.current.number == 3
    assert note.body == "from the first"


def test_restoring_appends_rather_than_rewinding():
    note = Note()
    note.revise("one")
    note.revise("two")
    restored = note.restore(1)

    assert restored.number == 3
    assert restored.base == 1
    assert note.body == "one"
    # The version it was restored over is still readable.
    assert note.version(2).body == "two"


def test_restoring_a_version_that_is_not_there_does_nothing():
    note = Note()
    note.revise("one")
    assert note.restore(9) is None
    assert len(note.versions) == 1


def test_writing_what_the_tip_already_says_appends_nothing():
    """Otherwise every focus-out adds a version identical to the one before it
    and the history is mostly noise."""
    note = Note()
    note.revise("one")
    again = note.revise("one")

    assert len(note.versions) == 1
    assert again.number == 1


def test_a_base_that_does_not_exist_falls_back_to_the_root():
    note = Note()
    note.revise("one")
    version = note.revise("two", base=99)
    assert version.base == 0


def test_children_of_a_version_are_every_edit_made_from_it():
    note = Note()
    note.revise("one")
    note.revise("two")
    note.revise("also from one", base=1)
    assert [v.number for v in note.children_of(1)] == [2, 3]


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
    """A sentence beginning with a dash is not a list item."""
    assert scratchpad.parse("-5 degrees")[0].kind == scratchpad.TEXT


def test_nesting_is_read_from_the_indent():
    body = "- top\n  - under\n    - deeper"
    assert [line.indent for line in scratchpad.parse(body)] == [0, 1, 2]


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
    assert scratchpad.toggle(body, 1) == body
    assert scratchpad.toggle(body, 99) == body


def test_changing_a_line_kind_keeps_its_words():
    body = "- milk"
    assert scratchpad.set_kind(body, 0, scratchpad.UNCHECKED) == "- [ ] milk"
    assert scratchpad.set_kind(body, 0, scratchpad.TEXT) == "milk"


def test_a_list_item_carries_its_marker_to_the_next_line():
    assert scratchpad.continuation("- [ ] milk") == "- [ ] "
    assert scratchpad.continuation("- milk") == "- "
    assert scratchpad.continuation("  - nested") == "  - "


def test_an_empty_item_ends_the_list():
    """A second Enter should stop the list rather than continue it forever."""
    assert scratchpad.continuation("- ") == ""
    assert scratchpad.continuation("- [ ] ") == ""
    assert scratchpad.continuation("prose") == ""


def test_progress_counts_only_checkboxes():
    assert scratchpad.progress("- [x] a\n- [ ] b\n- c\nd") == (1, 2)
    assert scratchpad.progress("no boxes here") == (0, 0)


def test_the_summary_is_the_first_line_with_anything_on_it():
    assert scratchpad.summary("\n\n- [ ] first real line") == "first real line"
    assert scratchpad.summary("") == ""


def test_a_long_summary_is_cut_rather_than_wrapped():
    assert len(scratchpad.summary("x" * 200, limit=20)) == 20


# -- the store ---------------------------------------------------------------


def test_notes_survive_a_round_trip_through_the_file(tmp_path):
    path = tmp_path / "scratchpad.json"
    store = NoteStore(path=path)
    note = store.create(title="Shopping", body="- [ ] milk")
    store.revise(note, "- [x] milk")
    store.revise(note, "- [ ] milk\n- [ ] eggs", base=1)

    reloaded = NoteStore(path=path)
    found = reloaded.get(note.id)
    assert found is not None
    assert [v.number for v in found.versions] == [1, 2, 3]
    assert found.version(3).base == 1
    assert found.body == "- [ ] milk\n- [ ] eggs"


def test_an_unreadable_file_does_not_stop_the_store_loading(tmp_path):
    path = tmp_path / "scratchpad.json"
    path.write_text("{ not json")
    assert NoteStore(path=path).notes == []


def test_a_file_of_the_wrong_shape_is_ignored(tmp_path):
    path = tmp_path / "scratchpad.json"
    path.write_text(json.dumps({"notes": "not a list"}))
    assert NoteStore(path=path).notes == []


def test_a_version_without_a_number_is_dropped_rather_than_guessed(tmp_path):
    path = tmp_path / "scratchpad.json"
    path.write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "id": "n1",
                        "title": "t",
                        "versions": [{"body": "no number"}, {"number": 2, "body": "ok"}],
                    }
                ]
            }
        )
    )
    note = NoteStore(path=path).get("n1")
    assert [v.number for v in note.versions] == [2]


def test_notes_belong_to_a_context_or_to_none(tmp_path):
    store = NoteStore(path=tmp_path / "s.json")
    mine = store.create(title="mine", context_id="ctx-1")
    everyones = store.create(title="everyones")

    assert store.notes_for("ctx-1") == [mine]
    assert store.globals() == [everyones]
    assert everyones.is_global and not mine.is_global


def test_the_two_settings_decide_which_notes_are_listed(tmp_path, monkeypatch):
    from context.state import settings

    store = NoteStore(path=tmp_path / "s.json")
    store.create(title="global one")
    store.create(title="context one", context_id="ctx-1")

    settings.update(scratchpad=True, scratchpad_global=True, scratchpad_per_context=True)
    assert {n.title for n in store.visible("ctx-1")} == {"global one", "context one"}

    settings.update(scratchpad_global=False)
    assert {n.title for n in store.visible("ctx-1")} == {"context one"}

    settings.update(scratchpad_global=True, scratchpad_per_context=False)
    assert {n.title for n in store.visible("ctx-1")} == {"global one"}

    settings.update(scratchpad=False)
    assert store.visible("ctx-1") == []


def test_searching_looks_at_the_body_as_well_as_the_title(tmp_path):
    store = NoteStore(path=tmp_path / "s.json")
    store.create(title="Shopping", body="- [ ] rutabaga")
    store.create(title="Ideas", body="- something else")

    assert [n.title for n in store.search("rutabaga")] == ["Shopping"]
    assert [n.title for n in store.search("shop")] == ["Shopping"]
    assert len(store.search("")) == 2


def test_deleting_removes_the_note_from_the_file(tmp_path):
    path = tmp_path / "s.json"
    store = NoteStore(path=path)
    note = store.create(title="gone")
    store.delete(note)
    assert NoteStore(path=path).notes == []


# -- the views ---------------------------------------------------------------


@needs_display
def test_the_sidebar_lists_notes_and_the_overview_lists_the_same_ones(gtk_app):
    """Two views of one thing: a note has to appear in both, or they drift the
    way the context list already did once."""
    from context.state.store import ContextStore
    from context.ui.rows import NoteRow
    from context.ui.window import LauncherWindow

    seen = {}

    def body(app):
        notes = NoteStore()
        notes.create(title="Shopping", body="- [ ] milk")
        store = ContextStore()

        window = LauncherWindow(app, store, lambda c: None, lambda c: None, notes=notes)
        window.refresh()
        seen["sidebar"] = [
            row.note.title
            for row in _rows(window.notes_listbox)
            if isinstance(row, NoteRow)
        ]

        from context.ui.overview import OverviewWindow

        overview = OverviewWindow(app, store, notes=notes)
        seen["overview"] = [
            row.note.title
            for row in _rows(overview.notes_list)
            if isinstance(row, NoteRow)
        ]
        overview.close()
        app.quit()

    run_app(gtk_app, body)
    assert seen["sidebar"] == ["Shopping"]
    assert seen["overview"] == ["Shopping"]


@needs_display
def test_the_sidebar_hides_notes_when_the_scratchpad_is_off(gtk_app):
    from context.state import settings
    from context.state.store import ContextStore
    from context.ui.window import LauncherWindow

    seen = {}

    def body(app):
        notes = NoteStore()
        notes.create(title="Shopping")
        window = LauncherWindow(
            app, ContextStore(), lambda c: None, lambda c: None, notes=notes
        )
        window.refresh()
        seen["on"] = window.notes_listbox.get_visible()

        settings.update(scratchpad=False)
        window.refresh()
        seen["off"] = window.notes_listbox.get_visible()
        app.quit()

    run_app(gtk_app, body)
    assert seen["on"] is True
    assert seen["off"] is False


@needs_display
def test_editing_an_old_version_in_the_editor_appends_a_new_one(gtk_app):
    """The store's guarantee, reached through the widgets that use it."""
    from context.ui.note_editor import NoteEditorPage

    seen = {}

    def body(app):
        notes = NoteStore()
        note = notes.create(title="Shopping", body="one")
        notes.revise(note, "two")
        notes.revise(note, "three")

        page = NoteEditorPage(notes, note, on_done=lambda n: None)
        page._show_version(1)
        seen["showing"] = page.body
        page.buffer.set_text("one, edited")
        page._commit()

        seen["numbers"] = [v.number for v in note.versions]
        seen["bases"] = [v.base for v in note.versions]
        seen["survived"] = [note.version(2).body, note.version(3).body]
        seen["tip"] = note.body
        app.quit()

    run_app(gtk_app, body)
    assert seen["showing"] == "one"
    assert seen["numbers"] == [1, 2, 3, 4]
    assert seen["bases"][-1] == 1
    assert seen["survived"] == ["two", "three"]
    assert seen["tip"] == "one, edited"


@needs_display
def test_ticking_a_box_in_the_checklist_rewrites_the_body(gtk_app):
    from gi.repository import Gtk

    from context.ui.note_editor import NoteEditorPage

    seen = {}

    def body(app):
        notes = NoteStore()
        note = notes.create(title="Shopping", body="- [ ] milk\n- [ ] bread")
        page = NoteEditorPage(notes, note, on_done=lambda n: None)
        page._rebuild_checklist()

        boxes = [w for w in _children(page.checklist) if isinstance(w, Gtk.CheckButton)]
        seen["count"] = len(boxes)
        boxes[0].set_active(True)
        seen["body"] = page.body
        app.quit()

    run_app(gtk_app, body)
    assert seen["count"] == 2
    assert seen["body"] == "- [x] milk\n- [ ] bread"


@needs_display
def test_the_editor_says_which_version_is_on_screen(gtk_app):
    """Editing from history is safe but surprising, so the page has to say it is
    happening rather than let it look like a rewind."""
    from context.ui.note_editor import NoteEditorPage

    seen = {}

    def body(app):
        notes = NoteStore()
        note = notes.create(title="n", body="one")
        notes.revise(note, "two")
        notes.revise(note, "three")

        page = NoteEditorPage(notes, note, on_done=lambda n: None)
        seen["at_tip"] = page.status.get_label()
        page._show_version(1)
        seen["in_history"] = page.status.get_label()
        seen["restore_offered"] = page.restore_button.get_visible()
        app.quit()

    run_app(gtk_app, body)
    assert "current" in seen["at_tip"]
    assert "version 1" in seen["in_history"].casefold()
    assert seen["restore_offered"] is True


def _children(box):
    found = []
    child = box.get_first_child()
    while child is not None:
        found.append(child)
        child = child.get_next_sibling()
    return found


def _rows(listbox):
    return _children(listbox)

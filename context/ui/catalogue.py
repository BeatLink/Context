"""Every installed application: searched, filtered by kind, grouped.

Two places offer applications and neither of them is about applications. The
overview asks *where should this open* — a context of its own, or the one you
came from. The editor asks *what should this context hold*. What a row does
differs; what is searched, filtered and grouped does not, so that part is here
and each caller supplies the row.

It was drawn twice before, and the two were not the same catalogue: the editor
had a search box over a flow of tiles with no way to narrow by kind and no
ordering at all, while the overview had all three. Finding an application
depended on which screen you happened to be looking at.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from context.state import settings, uistate
from context.ui import widgets
from context.system.apps import MAIN_CATEGORIES, SORTS, App, arrange_apps, categories_of
from context.system.apps import in_category, installed_apps, search_apps
from context.system.logging_setup import get_logger

log = get_logger("catalogue")


class AppCatalogue(Gtk.Box):
    """The application list, with the search and the two controls over it.

    `row_for(app)` builds one row — an `AppRow` in the overview, an `AddAppRow`
    in the editor. `counts()` is how many contexts each application belongs to,
    which only the "In contexts" grouping reads; without it that grouping still
    works and simply has nothing to split on.
    """

    def __init__(
        self,
        row_for,
        placeholder: str = "Search applications",
        counts=None,
        heading: str = "Apps",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.row_for = row_for
        self.counts = counts or dict
        self.heading = heading
        self.apps = installed_apps()
        # Which kind the list is showing; "" is all of them. Not a setting: it
        # is narrowing done in the moment, and a catalogue that opened filtered
        # would look like half the applications had gone.
        self.category = ""
        # The order is a setting, so it opens the same way every time rather
        # than however it was left.
        live = settings.current()
        self.sort = (
            live.overview_sort if live.overview_sort in SORTS else next(iter(SORTS))
        )
        self.flows: list[Gtk.ListBox] = []
        self._rows: dict[str, Gtk.Widget] = {}

        # The heading first, then the box that narrows it: "Apps · 93" is what
        # you are looking at, and the search is what you do to it.
        self.label = Gtk.Label(label=heading, xalign=0.0)
        self.label.add_css_class("heading")
        self.label.add_css_class("dim-label")
        self.append(self.label)

        self.entry = widgets.SearchBar(placeholder)
        self.entry.connect("search-changed", lambda _e: self.refresh())
        self.append(self.entry)

        # Buttons rather than a dropdown: both callers can be covered by a
        # layer-shell overlay, where a popover throws the click away. Only the
        # categories something is actually filed under — an empty "Science"
        # helps nobody.
        self.categories = ["", *categories_of(self.apps)]
        self.category_chooser = widgets.SegmentedChoice(self._on_category)
        for key in self.categories:
            self.category_chooser.add(MAIN_CATEGORIES.get(key, "All"))
        # The categories outrun the column on a full desktop, so this one
        # scrolls where the other row does not.
        category_scroller = Gtk.ScrolledWindow(
            propagate_natural_width=True, hexpand=True
        )
        category_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        category_scroller.set_child(self.category_chooser)
        # No "Category" label in front of it. The words in the buttons are the
        # categories, which says what the row is more directly than a label
        # would, and in a column that is half the editor the label was costing
        # the buttons the room they need.
        self.append(category_scroller)

        self.sort_keys = list(SORTS)
        self.sort_chooser = widgets.SegmentedChoice(self._on_sort)
        for key in self.sort_keys:
            self.sort_chooser.add(SORTS[key])
        self.sort_chooser.set_selected(self.sort_keys.index(self.sort), notify=False)
        # Left rather than stretched: without the label beside it there is
        # nothing holding it to its natural width.
        self.sort_chooser.set_halign(Gtk.Align.START)
        self.append(self.sort_chooser)

        # One section per group, each with its own list: a heading cannot sit
        # inside a list, and the groups are the point of the ordering.
        self.sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.sections)
        self.append(scroller)

        self.refresh()

    # -- the list ------------------------------------------------------------

    def refresh(self) -> None:
        within = in_category(self.apps, self.category)
        matches = search_apps(within, self.entry.get_text().strip())
        self._fill(
            arrange_apps(
                matches, self.sort, times=uistate.app_times(), counts=self.counts()
            )
        )
        kind = MAIN_CATEGORIES.get(self.category, "")
        self.label.set_label(f"{kind or self.heading} · {len(matches)}")

    def _fill(self, sections: list[tuple[str, list[App]]]) -> None:
        child = self.sections.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.sections.remove(child)
            child = following

        self.flows = []
        self._rows = {}
        for title, apps in sections:
            if title:
                label = Gtk.Label(label=title, xalign=0.0)
                label.add_css_class("heading")
                label.add_css_class("dim-label")
                self.sections.append(label)
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            listbox.set_valign(Gtk.Align.START)
            for info in apps:
                row = self.row_for(info)
                self._rows[info.id] = row
                listbox.append(row)
            self.sections.append(listbox)
            self.flows.append(listbox)

    def rows(self) -> list:
        """Every row on show, in the order the sections list them."""
        found = []
        for listbox in self.flows:
            child = listbox.get_first_child()
            while child is not None:
                found.append(child)
                child = child.get_next_sibling()
        return found

    def row(self, app_id: str):
        """One application's row, or None when the filter is hiding it."""
        return self._rows.get(app_id)

    def first(self):
        """The first row the search matched, for Enter."""
        for listbox in self.flows:
            row = listbox.get_row_at_index(0)
            if row is not None:
                return row
        return None

    # -- the controls --------------------------------------------------------

    def focus_search(self) -> None:
        self.entry.grab_focus()

    def clear(self) -> bool:
        """Undo the filtering. False when there was none, so a caller can tell
        an Escape that did something from one that should do something else."""
        if not self.entry.get_text():
            return False
        self.entry.set_text("")
        return True

    def _on_sort(self, selected: int) -> None:
        if 0 <= selected < len(self.sort_keys):
            self.sort = self.sort_keys[selected]
            log.debug("sorting apps by %s", self.sort)
            self.refresh()

    def _on_category(self, selected: int) -> None:
        if 0 <= selected < len(self.categories):
            self.category = self.categories[selected]
            log.debug("category %s", self.category or "all")
            self.refresh()

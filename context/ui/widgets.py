"""Plain GTK4 stand-ins for the libadwaita widgets Context used.

libadwaita is a GNOME house style before it is a widget set, and Context is a
desktop shell rather than a GNOME application. The mismatch was not academic:

  - It refuses to let an application choose light or dark independently of the
    system preference. That is a deliberate upstream position, and it is why
    the light theme could not be made to work through three attempts.
  - Its rows put the control beside the description, which is unusable at
    sidebar width. `_stacked` already had to route around that.
  - Its widgets carry Adwaita's own colours, which fight a theme read from the
    user's own file.

What was actually used is a handful of layout containers with no behaviour in
them, so they are reproduced here against Gtk directly. Everything keeps the
libadwaita method names — `add`, `set_child`, `add_top_bar` — so the call sites
did not have to be rewritten alongside the widgets.

Styling lives in `theme.css`; nothing here sets a colour.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, GObject, Gtk

from context.system.logging_setup import get_logger

log = get_logger("widgets")



class Page(Gtk.ScrolledWindow):
    """A scrolling column of groups.

    Replaces `Adw.PreferencesPage`. The width cap is what keeps the settings
    readable when the launcher is dragged wide, which is the one thing the
    libadwaita version did that a bare Box does not.
    """

    def __init__(self, max_width: int = 640) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.add_css_class("ctx-page")

        self._column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._column.set_margin_top(18)
        self._column.set_margin_bottom(18)
        self._column.set_margin_start(12)
        self._column.set_margin_end(12)

        # Capped but never given a floor: the launcher is often narrower than
        # any comfortable reading width, and a minimum wider than the sidebar
        # pushes the controls off the right-hand edge rather than shrinking
        # them. Filling and capping does both — wide windows stay readable,
        # narrow ones stay whole.
        self._column.set_halign(Gtk.Align.FILL)
        self._column.set_hexpand(True)
        self.set_max_content_width(max_width)
        self.set_child(self._column)

    def add(self, group: Gtk.Widget) -> None:
        self._column.append(group)


class Group(Gtk.Box):
    """A titled set of rows, drawn as one card.

    Replaces `Adw.PreferencesGroup`. Rows are separated by a line drawn in CSS
    rather than by widgets, so a row can be hidden without leaving a stray
    separator behind — which is what `_sync_rows` relies on when a setting
    stops applying.
    """

    def __init__(self, title: str = "", description: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        if title:
            label = Gtk.Label(label=title, xalign=0.0, wrap=True)
            label.add_css_class("ctx-group-title")
            self.append(label)

        self._description = None
        if description:
            self._description = Gtk.Label(label=description, xalign=0.0, wrap=True)
            self._description.add_css_class("ctx-group-description")
            self.append(self._description)

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("ctx-card")
        self.append(self._list)

    def add(self, row: Gtk.Widget) -> None:
        self._list.append(row)

    def remove(self, row: Gtk.Widget) -> None:
        self._list.remove(row)

    def set_description(self, text: str) -> None:
        if self._description is not None:
            self._description.set_label(text)


class Row(Gtk.ListBoxRow):
    """One setting.

    Replaces `Adw.PreferencesRow` and `Adw.ActionRow`. The title is kept as a
    property as well as drawn, because accessibility tooling and the tests both
    look rows up by name.
    """

    def __init__(self, title: str = "", activatable: bool = False) -> None:
        super().__init__(activatable=activatable, selectable=False)
        self._title = title
        self.set_tooltip_text(None)
        if title:
            self.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def get_title(self) -> str:
        return self._title


class ActionRow(Row):
    """A row with a title, a description, and things either side of them.

    Replaces `Adw.ActionRow`: an icon before, buttons after, and the text in
    between. Unlike the libadwaita version the title accepts markup, which the
    context list uses to mark the one you are in.

    Emits `activated` when clicked, matching the signal the call sites connect.
    """

    __gsignals__ = {"activated": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, title: str = "", subtitle: str = "", activatable: bool = False):
        super().__init__(title=title, activatable=activatable)
        self.add_css_class("ctx-row")

        self._box = Gtk.Box(spacing=10)
        self._box.set_margin_top(8)
        self._box.set_margin_bottom(8)
        self._box.set_margin_start(12)
        self._box.set_margin_end(12)

        self._prefixes = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self._box.append(self._prefixes)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.set_valign(Gtk.Align.CENTER)
        # An ellipsizing label asks for the width of its full text, and GTK
        # then shrinks it to whatever is left over — which at sidebar width
        # truncated titles to a few characters even with room to spare. A
        # small `max_width_chars` lowers what it asks for, so the label takes
        # the space actually available instead of being squeezed out of it.
        self._title = Gtk.Label(label=title, xalign=0.0, ellipsize=3, hexpand=True)
        self._title.set_max_width_chars(12)
        self._title.add_css_class("ctx-row-title")
        text.append(self._title)

        self._subtitle = Gtk.Label(label=subtitle, xalign=0.0, ellipsize=3, hexpand=True)
        self._subtitle.set_max_width_chars(12)
        self._subtitle.add_css_class("ctx-row-subtitle")
        self._subtitle.set_visible(bool(subtitle))
        text.append(self._subtitle)
        self._box.append(text)

        self._suffixes = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self._box.append(self._suffixes)

        self._activatable_widget: Gtk.Widget | None = None
        self._forwarding = False
        self._click: Gtk.GestureClick | None = None

        Row.set_child(self, self._box)

        if activatable:
            self.set_activatable(True)

    def set_activatable(self, activatable: bool) -> None:
        """Arm or disarm the click that emits `activated`.

        Overridden because the GTK property alone changes nothing here: rows
        built plain and made activatable afterwards — the context list, the
        switcher — otherwise looked activatable and never emitted, so clicking
        a context silently did nothing while the row's buttons kept working.
        """
        self.set_property("activatable", activatable)
        if activatable and self._click is None:
            self._click = Gtk.GestureClick()
            self._click.connect("released", lambda *_a: self.emit("activated"))
            self.add_controller(self._click)
        elif not activatable and self._click is not None:
            self.remove_controller(self._click)
            self._click = None

    def set_title(self, title: str) -> None:
        self._title.set_label(title)
        self._title_text = title

    def get_title(self) -> str:
        return self._title.get_label()

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle.set_label(subtitle)
        self._subtitle.set_visible(bool(subtitle))

    def get_subtitle(self) -> str:
        return self._subtitle.get_label()

    def set_use_markup(self, use: bool) -> None:
        self._title.set_use_markup(use)

    def add_prefix(self, widget: Gtk.Widget) -> None:
        self._prefixes.append(widget)

    def add_suffix(self, widget: Gtk.Widget) -> None:
        self._suffixes.append(widget)

    def link_suffixes(self) -> None:
        """Draw the suffix buttons as one linked control rather than loose icons.

        Call it once every suffix is on. `.linked` rounds by position among the
        box's children, and `:first-child` is structural rather than visual, so
        a button that is present but hidden would leave its neighbour square on
        one side — only add the ones that are going to show.

        The frame has to come back for the join to be visible at all, so `flat`
        is dropped here rather than at each call site.
        """
        self._suffixes.set_spacing(0)
        self._suffixes.add_css_class("linked")
        child = self._suffixes.get_first_child()
        while child is not None:
            child.remove_css_class("flat")
            child = child.get_next_sibling()

    def set_activatable_widget(self, widget: Gtk.Widget | None) -> None:
        """Clicking the row acts on this widget — usually a switch.

        libadwaita forwards the activation itself; here the row already emits
        `activated`, so this only has to say what to forward it to.
        """
        self._activatable_widget = widget
        self.set_activatable(widget is not None)
        if widget is not None and not self._forwarding:
            self._forwarding = True
            self.connect("activated", lambda _r: self._activate_widget())

    def _activate_widget(self) -> None:
        widget = getattr(self, "_activatable_widget", None)
        if isinstance(widget, Gtk.Switch):
            widget.set_active(not widget.get_active())
        elif widget is not None:
            widget.activate()

    def set_subtitle_lines(self, lines: int) -> None:
        """How many lines the description may wrap to. 0 means no limit."""
        self._subtitle.set_wrap(lines != 1)
        self._subtitle.set_lines(lines)
        self._subtitle.set_ellipsize(3 if lines else 0)


class EntryRow(Row):
    """A row that is a text field.

    Replaces `Adw.EntryRow`, whose label floats into the entry when it has
    content. Here the label sits above it, which is the same arrangement
    `_stacked` uses for every other setting.
    """

    def __init__(self, title: str = "") -> None:
        super().__init__(title=title)
        box = Gtk.Box(spacing=8)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        label = Gtk.Label(label=title, xalign=0.0)
        label.add_css_class("ctx-row-subtitle")
        column.append(label)

        self.entry = Gtk.Entry(hexpand=True)
        column.append(self.entry)
        box.append(column)

        # A URL row carries a remove button beside its field, so suffixes are
        # needed here too — the same slot `ActionRow` puts its buttons in.
        self._suffixes = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        box.append(self._suffixes)

        Row.set_child(self, box)

    def add_suffix(self, widget: Gtk.Widget) -> None:
        self._suffixes.append(widget)

    def link_suffixes(self) -> None:
        """Draw the suffix buttons as one linked control rather than loose icons.

        Call it once every suffix is on. `.linked` rounds by position among the
        box's children, and `:first-child` is structural rather than visual, so
        a button that is present but hidden would leave its neighbour square on
        one side — only add the ones that are going to show.

        The frame has to come back for the join to be visible at all, so `flat`
        is dropped here rather than at each call site.
        """
        self._suffixes.set_spacing(0)
        self._suffixes.add_css_class("linked")
        child = self._suffixes.get_first_child()
        while child is not None:
            child.remove_css_class("flat")
            child = child.get_next_sibling()

    def add_prefix(self, widget: Gtk.Widget) -> None:
        self._suffixes.prepend(widget)

    def get_text(self) -> str:
        return self.entry.get_text()

    def set_text(self, text: str) -> None:
        self.entry.set_text(text)

    def grab_focus(self) -> bool:
        return self.entry.grab_focus()

    def connect(self, signal: str, handler, *args):
        """Signals about the text belong to the entry, not the row.

        `changed` and `activate` are emitted by `Gtk.Entry`, so connecting them
        on the row would silently never fire — the row has no such signal.
        """
        if signal in ("changed", "activate"):
            return GObject.Object.connect(self.entry, signal, handler, *args)
        return super().connect(signal, handler, *args)


class ToolbarView(Gtk.Box):
    """Content with bars above it.

    Replaces `Adw.ToolbarView`. Only top bars were ever used.
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._content: Gtk.Widget | None = None

    def add_top_bar(self, bar: Gtk.Widget) -> None:
        self.prepend(bar)

    def set_content(self, content: Gtk.Widget) -> None:
        if self._content is not None:
            self.remove(self._content)
        self._content = content
        content.set_vexpand(True)
        self.append(content)


class HeaderBar(Gtk.CenterBox):
    """A title with room for buttons either side.

    Replaces `Adw.HeaderBar`. Context suppressed its window controls
    everywhere — the launcher is a docked layer surface with nothing to
    minimise — so this has none to suppress.
    """

    def __init__(self, title: str = "") -> None:
        super().__init__()
        self.add_css_class("ctx-header")

        self._start = Gtk.Box(spacing=6)
        self._end = Gtk.Box(spacing=6)
        self._label = Gtk.Label(label=title, single_line_mode=True)
        self._label.add_css_class("ctx-header-title")

        self.set_start_widget(self._start)
        self.set_center_widget(self._label)
        self.set_end_widget(self._end)

    def pack_start(self, widget: Gtk.Widget) -> None:
        self._start.append(widget)

    def pack_end(self, widget: Gtk.Widget) -> None:
        self._end.append(widget)

    def set_title(self, title: str) -> None:
        self._label.set_label(title)

    # Accepted and ignored: a layer surface has no window controls to show.
    def set_show_start_title_buttons(self, _show: bool) -> None: ...
    def set_show_end_title_buttons(self, _show: bool) -> None: ...
    def set_title_widget(self, widget: Gtk.Widget) -> None:
        self.set_center_widget(widget)


class SegmentedChoice(Gtk.Box):
    """One of several, as buttons rather than a list behind a popover.

    The editor is a layer-shell overlay, and a `Gtk.DropDown` there opens its
    list but never keeps what is clicked in it — the popup closes with the old
    value still selected, so both layout dropdowns appeared to do nothing.
    Buttons live in the surface itself and have no such problem; on a screen
    this size the choices are better shown than hidden anyway.
    """

    def __init__(self, on_change) -> None:
        # No spacing: `.linked` joins the buttons into one control, and a gap
        # between them is exactly what stops that reading.
        super().__init__(spacing=0)
        self.add_css_class("linked")
        self.on_change = on_change
        self._buttons: list[Gtk.ToggleButton] = []
        self._selected = 0
        # Set while the buttons are being brought in line with the choice, so
        # untoggling the old one is not read as a choice of its own.
        self._settling = False

    def add(self, child, tooltip: str = "") -> Gtk.ToggleButton:
        button = Gtk.ToggleButton()
        # Adwaita's checked state is a shade darker, which on a thumbnail of a
        # window arrangement is invisible; the theme rings the chosen one.
        button.add_css_class("ctx-choice")
        if isinstance(child, str):
            button.set_label(child)
        else:
            button.set_child(child)
        if tooltip:
            button.set_tooltip_text(tooltip)
        index = len(self._buttons)
        button.set_active(index == self._selected)
        button.connect("toggled", lambda b, i=index: self._on_toggled(b, i))
        self._buttons.append(button)
        self.append(button)
        return button

    def clear(self) -> None:
        """Empty the row, for a set of choices that depends on something else."""
        for button in self._buttons:
            self.remove(button)
        self._buttons = []
        self._selected = -1

    def _on_toggled(self, button: Gtk.ToggleButton, index: int) -> None:
        if self._settling:
            return
        if not button.get_active():
            # Clicking the chosen one again must not leave nothing chosen.
            self._settle(self._selected)
            return
        self._selected = index
        self._settle(index)
        self.on_change(index)

    def _settle(self, index: int) -> None:
        self._settling = True
        for position, button in enumerate(self._buttons):
            button.set_active(position == index)
        self._settling = False

    def set_selected(self, index: int, notify: bool = True) -> None:
        """Choose one, or -1 for none — which is what a hand-dragged layout is."""
        if index >= len(self._buttons):
            return
        if index < 0:
            self._selected = -1
            self._settle(-1)
            return
        self._selected = index
        self._settle(index)
        if notify:
            self.on_change(index)

    def get_selected(self) -> int:
        return self._selected

    def set_choice_label(self, index: int, label: str) -> None:
        """Rename one choice, for a label that says what it will act on."""
        if 0 <= index < len(self._buttons):
            self._buttons[index].set_label(label)

    def set_choice_sensitive(self, index: int, sensitive: bool) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].set_sensitive(sensitive)


class NavigationPage(Gtk.Box):
    """One screen in a navigation stack.

    Replaces `Adw.NavigationPage`. The tag is how a page is found again without
    holding a reference to it, which is what `pop_to_tag` and `find_page` use.
    """

    def __init__(self, child: Gtk.Widget | None = None, title: str = "", tag: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._title = title
        self._tag = tag
        self._child: Gtk.Widget | None = None
        if child is not None:
            self.set_child(child)

    def set_child(self, child: Gtk.Widget) -> None:
        if self._child is not None:
            self.remove(self._child)
        self._child = child
        child.set_vexpand(True)
        self.append(child)

    def get_child(self) -> Gtk.Widget | None:
        return self._child

    def get_tag(self) -> str:
        return self._tag

    def get_title(self) -> str:
        return self._title


class NavigationView(Gtk.Stack):
    """A stack of pages with back-navigation.

    Replaces `Adw.NavigationView`. A `Gtk.Stack` already does the showing and
    the transition; what libadwaita adds on top is the stack discipline — push,
    pop, and finding a page by tag — which is this class.

    Pages pushed more than once are moved rather than duplicated, because
    opening settings twice from different depths otherwise leaves two of them
    in the history and needs two backs to leave.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack: list[NavigationPage] = []

    def add(self, page: NavigationPage) -> None:
        """Add the root page. Anything after the first is a push."""
        if self._stack:
            self.push(page)
            return
        self.add_named(page, page.get_tag() or f"page-{len(self._stack)}")
        self._stack.append(page)
        self.set_visible_child(page)

    def push(self, page: NavigationPage) -> None:
        existing = self.find_page(page.get_tag()) if page.get_tag() else None
        if existing is not None:
            self._stack.remove(existing)
            self._stack.append(existing)
            self.set_visible_child(existing)
            return
        name = page.get_tag() or f"page-{len(self._stack)}"
        self.add_named(page, name)
        self._stack.append(page)
        self.set_visible_child(page)

    def pop(self) -> bool:
        """Go back one page. The root page cannot be popped."""
        if len(self._stack) < 2:
            return False
        page = self._stack.pop()
        self.set_visible_child(self._stack[-1])
        self.remove(page)
        return True

    def pop_to_tag(self, tag: str) -> bool:
        for index, page in enumerate(self._stack):
            if page.get_tag() == tag:
                for gone in self._stack[index + 1 :]:
                    self.remove(gone)
                del self._stack[index + 1 :]
                self.set_visible_child(page)
                return True
        return False

    def find_page(self, tag: str) -> NavigationPage | None:
        for page in self._stack:
            if page.get_tag() == tag:
                return page
        return None

    def get_visible_page(self) -> NavigationPage | None:
        return self._stack[-1] if self._stack else None


class StatusPage(Gtk.Box):
    """What is shown when a list is empty.

    Replaces `Adw.StatusPage`.
    """

    def __init__(self, title: str = "", description: str = "", icon_name: str = ""):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
            vexpand=True,
        )
        self.add_css_class("ctx-status")
        self.set_margin_start(24)
        self.set_margin_end(24)

        self._icon = Gtk.Image(icon_name=icon_name or "", pixel_size=64)
        self._icon.add_css_class("ctx-status-icon")
        self._icon.set_visible(bool(icon_name))
        self.append(self._icon)

        # Always built, never conditionally: the empty state is rewritten when
        # a search matches nothing, so the labels have to exist to be set.
        self._title = Gtk.Label(
            label=title, wrap=True, justify=Gtk.Justification.CENTER, visible=bool(title)
        )
        self._title.add_css_class("ctx-status-title")
        self.append(self._title)

        self._description = Gtk.Label(
            label=description,
            wrap=True,
            justify=Gtk.Justification.CENTER,
            visible=bool(description),
        )
        self._description.add_css_class("ctx-status-description")
        self.append(self._description)

    def set_title(self, title: str) -> None:
        self._title.set_label(title)
        self._title.set_visible(bool(title))

    def set_description(self, description: str) -> None:
        self._description.set_label(description)
        self._description.set_visible(bool(description))

    def set_icon_name(self, icon_name: str) -> None:
        self._icon.set_from_icon_name(icon_name)
        self._icon.set_visible(bool(icon_name))

    def set_child(self, child: Gtk.Widget) -> None:
        self.append(child)

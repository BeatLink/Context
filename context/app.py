"""Application entrypoint for the Context launcher."""

from __future__ import annotations

import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from context.state import uistate
from context.system import backends, notify
from context.ui import switcher
from context.state.resources import Resource
from context.system.backends import Workspace
from context.system.launcher import active_context, adopt_loose, capture_arrangement, close_loose
from context.system.launcher import current_context, go_home, is_home_context
from context.system.launcher import restore_arrangement
from context.system.launcher import open_state
from context.system.launcher import has_drifted, is_no_context
from context.system.launcher import move_window_to_context, move_window_to_screen
from context.system.launcher import unmanaged_windows
from context.system.launcher import close_context as close_ctx
from context.system.launcher import context_is_open, launch_resource
from context.system.launcher import hand_keyboard_back
from context.system.launcher import launch_context as launch_ctx
from context.system.launcher import reconnect
from context.state.scratchpad import NoteStore
from context.state.store import Context, ContextStore
from context.system.logging_setup import configure, get_logger
from context.ui.window import LauncherWindow


# How long to wait for monitor hot-plug to settle before rebuilding. Enabling a
# screen emits several changes as modes are negotiated; rebuilding on each one
# tears the launchers down mid-change.
MONITOR_SETTLE_MS = 400

# The overview window's title. The compositor matches on it to place the window
# on home, so it is one constant rather than a string in two places.
OVERVIEW_TITLE = "Overview"


# Commands a keybind can send to the running instance. `python3 -m context
# switch-window` hands its command line over D-Bus rather than starting a
# second copy, so these cost nothing to bind.
COMMANDS = {
    "switch": lambda app: app.switch_context(),
    "switch-window": lambda app: app.switch_window(),
    "switch-window-all": lambda app: app.switch_window_all(),
    "previous": lambda app: app.previous_context(),
    "settings": lambda app: app.open_settings(),
    "overview": lambda app: app.open_overview(),
    "toggle-rail": lambda app: app.toggle_collapsed(),
    "restart": lambda app: app.restart(),
    # Window management. These act on the focused window, so they are bound to
    # keys rather than driven from the launcher.
    "move-window": lambda app: app.move_window_to_context(),
    "adopt": lambda app: app.adopt_windows(),
    "capture": lambda app: app.capture_context(),
    "window-left": lambda app: app.throw_window(-1),
    "window-right": lambda app: app.throw_window(1),
    "fullscreen": lambda app: app.window_state("fullscreen"),
    "maximise": lambda app: app.window_state("maximise"),
    "float": lambda app: app.window_state("float"),
    "tile": lambda app: app.window_state("tile"),
    "center": lambda app: app.window_state("center"),
    "group": lambda app: app.group_window(),
    "ungroup": lambda app: app.ungroup_window(),
}


class ContextApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id="io.beatlink.Context",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        configure()
        self.log = get_logger("app")
        self.store = ContextStore()
        # One note store for every launcher and the overview, for the same
        # reason the context store is shared: a note written on one screen has
        # to be there on the others.
        self.notes = NoteStore()
        self.backend = backends.detect()
        self.log.info("backend: %s", self.backend.name)
        # The primary launcher, and the extras when it is shown on every
        # screen. `window` stays the one everything else talks to — the editor
        # and refreshes go to it — while `windows` is what gets kept in step.
        self.window: LauncherWindow | None = None
        self.extra_windows: list[LauncherWindow] = []
        # Contexts already offered a save this run, so a drifting one that is
        # left unsaved does not ask again at every opportunity.
        self.asked_about: set[str] = set()
        self.launching: set[str] = set()
        self.switcher: switcher.SwitcherWindow | None = None
        # The overview, built once and then never closed. Deliberately not the
        # `switcher` slot: that is whatever overlay is up at the moment, and
        # home is a place that outlives all of them.
        self.overview = None
        # Held so the monitor list is not collected while it is being watched.
        self._monitor_model = None
        self._monitor_settle = 0
        # Whether anything was open last time it was looked at, so the overview
        # appears when the last context closes rather than on every check.
        self._had_open = True

    def do_command_line(self, command_line) -> int:
        """Entry point for every launch, first or subsequent.

        GApplication already enforces a single instance — a second launch hands
        its command line to the first over D-Bus and exits. That was previously
        silent, which made a stale instance look like a broken build: relaunches
        appeared to do nothing. Now the running instance says so and focuses the
        context in view, and the launcher is presented rather than duplicated.

        The hand-off is also how keybinds reach a running Context: a bind runs
        `python3 -m context switch-window`, whose command line arrives here.
        """
        argv = command_line.get_arguments()[1:]
        command = argv[0] if argv else ""

        if command in COMMANDS:
            self.log.info("command: %s", command)
            # A command needs the window built, but must not raise the launcher
            # over the picker it is about to open.
            self.ensure_window()
            try:
                COMMANDS[command](self)
            except Exception:
                # A failing command must not take the launcher down with it.
                self.log.exception("command %s failed", command)
            return 0

        if command:
            self.log.warning("unknown command %r; opening the launcher", command)
        if self.window is not None:
            self.log.info("already running; focusing the existing launcher")
            self._focus_active_context()
        self.activate()
        return 0

    # -- commands ------------------------------------------------------------

    def ensure_window(self) -> LauncherWindow:
        if self.window is None:
            self.do_activate()
        return self.window

    def switch_context(self) -> None:
        self._open_switcher(switcher.CONTEXTS)

    def switch_window(self) -> None:
        self._open_switcher(switcher.WINDOWS)

    def switch_window_all(self) -> None:
        self._open_switcher(switcher.WINDOWS, scope_all=True)

    def _open_switcher(self, mode: str, scope_all: bool = False) -> None:
        """Open a picker, replacing any already up.

        Without this a keybind pressed twice stacks a second full-screen
        overlay on the first — and since each one takes the keyboard
        exclusively, the pile has to be dismissed one layer at a time.
        """
        existing = self.switcher
        if existing is not None:
            existing.close()
            self.switcher = None
            # The same bind again means "put it away", not "open another".
            if existing.mode == mode and existing.scope_all == scope_all:
                return

        picker = switcher.SwitcherWindow(self, self.store, mode, scope_all)
        picker.on_context = self.go_to_context
        picker.connect("close-request", self._on_switcher_closed)
        self.switcher = picker
        picker.present()

    def _on_switcher_closed(self, _window) -> bool:
        self.switcher = None
        # The overlay held the keyboard exclusively, and Hyprland reports the
        # window active again on unmap without re-sending the keyboard enter.
        # Handing it back explicitly is what actually revives typing. A picker
        # that goes on to focus or launch something overrides this immediately.
        hand_keyboard_back(backend=self.backend)
        return False

    def ensure_overview(self):
        """The overview window, built once and kept.

        It is the window that lives on the home workspace, so throwing it away
        and making another would be leaving home empty.
        """
        if self.overview is not None:
            return self.overview
        from context.ui.overview import OverviewWindow

        # Before the window exists, because the rule is what decides where it
        # maps. Arranging the switch first instead loses a race that cannot be
        # won from here: `present()` returns long before the surface is
        # committed, and the sidebar handing the keyboard back in between moves
        # the active workspace out from under the map — which is exactly how
        # the overview ended up mapped in whatever context you came from.
        app_id = self.get_application_id()
        self.backend.bind_to_home(app_id, OVERVIEW_TITLE)
        # No titlebar either: home is a fixture, not a window you manage, and
        # a compositor-drawn bar offers to close and move the one window that
        # must not go anywhere.
        self.backend.hide_titlebar(app_id, OVERVIEW_TITLE)

        window = OverviewWindow(self, self.store, backend=self.backend)
        # Three, where there were seven: everything a *context* can be asked to
        # do — edit, close, forget, save, restore, take an app — is the
        # sidebar's now, and the sidebar stands open beside home.
        window.on_context = self.go_to_context
        window.on_app_into = self.add_app_to_context
        window.on_leave = self.leave_home
        # Only if something destroys it anyway — `restart` does, by dropping
        # `permanent` first. Anything else that manages it means home has no
        # window, so the next visit builds a new one rather than switching to
        # an empty workspace.
        window.connect("destroy", self._on_overview_destroyed)
        self.overview = window
        return window

    def _on_overview_destroyed(self, _window) -> None:
        self.overview = None

    def prepare_home(self) -> None:
        """Put the overview on home before anything asks for it.

        Its window is what makes home a place rather than an empty workspace,
        and it is built rather than mapped instantly — reading every installed
        application takes long enough to be raced. Doing it at startup means
        going home is only ever a workspace switch.

        On idle rather than inline: the launcher should be on screen first, and
        this costs the application list. The window rule carries `silent`, so it
        maps on home without moving you off whatever you are working in.
        """
        window = self.ensure_overview()
        window.present()

    def open_overview(self) -> None:
        """Go home: contexts one side, applications the other.

        A workspace switch, not a window being raised — so the same keybind
        again does not put it away, because there is nothing to put away.
        """
        window = self.ensure_overview()
        window.refresh()
        if not go_home(backend=self.backend):
            # No workspaces to switch between, so home is only a window. Under
            # the null backend that is the whole of what a context is too.
            self.log.debug("no home workspace; showing the overview as a window")
        window.present()
        # After the switch, so the launchers read the workspace they are now
        # on: the sidebar stands open on home, and waiting for the next poll to
        # notice would have it widen a second or two after you arrived.
        self.refresh_all()

    def leave_home(self) -> None:
        """Back to the context you came from, if it is still open.

        Nothing happens when there is nowhere to go. Home is what a desktop
        with nothing running looks like, so leaving it would be leaving the
        only screen with anything on it.
        """
        target = current_context(self.store.contexts, backend=self.backend)
        if target is None:
            self.log.info("nowhere to go back to; staying home")
            return
        self.go_to_context(target)

    def edit_context(self, ctx: Context, is_new: bool = False) -> None:
        """Open a context's editor, whichever view asked for it."""
        window = self.ensure_window()
        if window is not None:
            window.edit_context(ctx, is_new=is_new)

    def edit_note(self, showing=None) -> None:
        """Open the scratchpad with room, whichever view asked for it."""
        window = self.ensure_window()
        if window is not None:
            window._open_note(showing)

    def open_settings(self) -> None:
        """The settings screen, replacing whatever picker is up.

        The same keybind again puts it away, the way the overview does.
        """
        from context.ui.settings_window import SettingsWindow

        existing = self.switcher
        if isinstance(existing, SettingsWindow):
            existing.close()
            self.switcher = None
            return
        self._show_picker(SettingsWindow(self, self.ensure_window()))

    def restore_context(self, ctx: Context) -> None:
        """Put a drifted context back, on a worker thread.

        It relaunches whatever is missing, so it is as slow as opening a context
        and for the same reason: each application is waited for. On the main
        loop that froze the launcher for the duration. Nothing in the worker may
        touch GTK; the result comes back through `idle_add`.
        """
        if is_no_context(ctx):
            return
        if ctx.id in self.launching:
            self.log.info("%s is already being launched", ctx.title)
            return
        self.launching.add(ctx.id)
        threading.Thread(
            target=self._restore_worker, args=(ctx,), daemon=True
        ).start()

    def _restore_worker(self, ctx: Context) -> None:
        try:
            launched, moved, screens = restore_arrangement(ctx, backend=self.backend)
        except Exception as exc:  # pragma: no cover - depends on the compositor
            self.log.exception("restoring %s failed", ctx.title)
            launched, moved, screens = 0, 0, 0
            GLib.idle_add(
                lambda: self._restored(ctx, 0, 0, f"Could not restore “{ctx.title}”: {exc}")
            )
            return
        parts = []
        if launched:
            parts.append(f"reopened {launched}")
        if moved:
            parts.append(f"moved {moved}")
        message = (
            f"Put “{ctx.title}” back — {', '.join(parts)}"
            if parts
            else f"Nothing to put back for “{ctx.title}”"
        )
        GLib.idle_add(lambda: self._restored(ctx, launched, moved, message))

    def _restored(self, ctx: Context, launched: int, moved: int, message: str) -> bool:
        self.launching.discard(ctx.id)
        self.asked_about.discard(ctx.id)
        notify.withdraw(self, "drift")
        notify.send(self, "restore", message)
        self.refresh_all()
        return False

    def open_app_in_context(self, ctx: Context) -> None:
        """Pick an application and open it inside an existing context.

        The app joins the context rather than only being started in its
        workspace: "open this here" almost always means it belongs here, and a
        context that forgets it the moment it is closed would have to be told
        again every time.
        """
        from context.ui.app_picker import AppGridWindow

        picker = AppGridWindow(
            self,
            f"Open in “{ctx.title}”",
            lambda info: self.add_app_to_context(ctx, info),
            subtitle="The app joins this context and opens in it.",
        )
        self._show_picker(picker)

    def add_app_to_context(self, ctx: Context, info) -> None:
        ctx.resources.append(Resource(app_id=info.id))
        self.store.save()
        self.log.info("added %s to %s", info.id, ctx.title)
        if not context_is_open(ctx, backend=self.backend):
            # Closed: opening it launches everything, the new app included.
            self.launch_context(ctx)
            return
        # Open: exactly the one window joins it. Launching the whole context
        # would be a switch under the new model, and the old relaunch-missing
        # model rearranged what the user already had.
        index = len(ctx.resources) - 1
        if ctx.id in self.launching:
            self.log.info("%s is already being launched", ctx.title)
            return
        self.launching.add(ctx.id)
        threading.Thread(
            target=self._resource_worker, args=(ctx, index), daemon=True
        ).start()

    def _resource_worker(self, ctx: Context, index: int) -> None:
        try:
            result = launch_resource(ctx, index, backend=self.backend)
        except Exception:
            self.log.exception("launching into %s failed", ctx.title)
            result = None
        GLib.idle_add(self._launch_finished, ctx, result)

    def add_app_to_active(self, info) -> bool:
        """Add an app to the context an action means. False when there is none.

        `current_context`, not `active_context`: the sidebar's app results are
        reachable from home, where nothing is active and the context meant is
        the one you came from.
        """
        current = current_context(self.store.contexts, backend=self.backend)
        if current is None:
            return False
        self.add_app_to_context(current, info)
        return True

    def note_open_contexts(self, count: int) -> None:
        """Go home when the last context closes, and only then.

        On the transition, not on every check: once you have walked away from
        home, an empty desktop should stay where you left it until something
        opens and closes again.
        """
        if count:
            self._had_open = True
            return
        if not self._had_open:
            return
        self._had_open = False
        if self.switcher is not None or self._covered():
            # An editor or a picker is up. Switching the workspace underneath
            # one would be invisible until it closed, and would then have moved
            # the user somewhere they did not ask to go.
            return
        self.log.info("nothing is open; going home")
        self.open_overview()

    def _covered(self) -> bool:
        """Whether an editor or a picker is on screen.

        The launchers are excluded because they are always there, and so is the
        overview now that it is: it is what going home *shows*, so counting it
        as something in the way meant home was never reached again after the
        first visit.
        """
        ignored = set(self.launchers)
        if self.overview is not None:
            ignored.add(self.overview)
        return any(
            window.get_visible() for window in self.get_windows() if window not in ignored
        )

    def restart(self) -> None:
        """Replace this process with a fresh one.

        Several settings — the edge, the backend, the log level — are read once
        at startup, and the page says so rather than pretending otherwise. This
        is what acts on that, so changing one does not mean finding the terminal
        it was launched from.

        `execv` rather than spawn-and-quit: the new process inherits the same
        pid, so anything watching Context (a systemd unit, a shell job) sees a
        restart rather than a disappearance. Contexts that are open stay open
        and are picked up again by `reconnect` on the way back.
        """
        argv = [sys.executable, "-m", "context", *sys.argv[1:]]
        self.log.info("restarting: %s", " ".join(argv))
        # Windows first: an execv leaves surfaces behind if the compositor is
        # not told, and the launcher would come back beside its own ghost. The
        # overview refuses to close, so it is released here — this is the one
        # thing entitled to take it down, and it puts it straight back.
        if self.overview is not None:
            self.overview.permanent = False
        for window in list(self.get_windows()):
            window.close()

        def replace() -> bool:
            try:
                os.execv(sys.executable, argv)
            except OSError as exc:
                self.log.error("could not restart: %s", exc)
            return False

        # After the current dispatch, so the windows are actually gone first.
        GLib.idle_add(replace)

    # -- window management ---------------------------------------------------

    def focused_window(self):
        """The window a keybind should act on."""
        found = self.backend.windows()
        return found[0] if found else None

    def window_state(self, state: str) -> None:
        window = self.focused_window()
        if window is None:
            return
        if not self.backend.set_window_state(window.id, state):
            self.log.info("could not %s %s", state, window.title or window.app_id)

    def group_window(self) -> None:
        window = self.focused_window()
        if window is not None:
            self.backend.group_windows(window.id)

    def ungroup_window(self) -> None:
        window = self.focused_window()
        if window is not None:
            self.backend.ungroup_window(window.id)

    def throw_window(self, direction: int) -> None:
        """Move the focused window to the context's next or previous screen."""
        window = self.focused_window()
        current = active_context(self.store.contexts, backend=self.backend)
        if window is None or current is None:
            return
        if not move_window_to_screen(
            window.id, current, direction, backend=self.backend
        ):
            self.log.info("%s has no other screen to move to", current.title)

    def move_window_to_context(self) -> None:
        """Pick a context and send the focused window into it."""
        window = self.focused_window()
        if window is None:
            return
        picker = switcher.SwitcherWindow(self, self.store, switcher.CONTEXTS)
        picker.set_title(f"Move “{window.title or window.app_id}” to")
        picker.on_context = lambda ctx: self._finish_move(window, ctx)
        self._show_picker(picker)

    def _finish_move(self, window, ctx: Context) -> None:
        if move_window_to_context(window.id, ctx, backend=self.backend):
            self.log.info("moved %s to %s", window.app_id, ctx.title)
        else:
            notify.send(
                self, "move", "Nothing to move into", f"Open “{ctx.title}” first"
            )

    def adopt_windows(self) -> None:
        """Offer every window that belongs to no context a home."""
        loose = unmanaged_windows(self.store.contexts, backend=self.backend)
        if not loose:
            self.log.info("every window already belongs to a context")
            notify.send(self, "adopt", "Nothing to adopt", "Every window is in a context")
            return
            from context.ui.adopt import AdoptWindow

        picker = AdoptWindow(self, self.store, loose, self.backend)
        self._show_picker(picker)

    def capture_context(self) -> None:
        """Save what the current context has become — the keybind for the
        button a drifted context grows in the list."""
        current = active_context(self.store.contexts, backend=self.backend)
        if current is None:
            self.log.info("not in a context")
            return
        self.save_context(current)

    def _show_picker(self, picker) -> None:
        existing = self.switcher
        if existing is not None:
            existing.close()
        picker.connect("close-request", self._on_switcher_closed)
        self.switcher = picker
        picker.present()

    def previous_context(self) -> None:
        """Alt-tab between the last two contexts."""
        current = active_context(self.store.contexts, backend=self.backend)
        wanted = uistate.previous_context(current.id if current else None)
        target = next((c for c in self.store.contexts if c.id == wanted), None)
        if target is None:
            self.log.info("no previous context to return to")
            self.activate()
            return
        self.go_to_context(target)

    def go_to_context(self, ctx: Context) -> None:
        """Switch to a context, launching it if its windows are gone."""
        if is_home_context(ctx):
            # Home has nothing to launch and no definition to visit: going
            # there is the workspace switch, and the window is already on it.
            self.open_overview()
            return
        if is_no_context(ctx):
            # Nothing to launch — these windows are already open, and there is
            # no workspace of their own to switch to. Going there means going
            # to the first of them.
            self.focus_loose(ctx)
            return
        # Before leaving: the context being left is the one that drifted, and
        # switching away is the last chance to notice.
        self.offer_to_save("switch", leaving=ctx)
        uistate.note_visit(ctx.id)
        self.launch_context(ctx)

    # -- saving what a context became ----------------------------------------

    def offer_to_save(self, moment: str, leaving: Context | None = None) -> bool:
        """Ask whether to keep how a context has been rearranged.

        `moment` is what just happened — a change, a switch, a close. It only
        asks when that is the moment the user chose, so the setting is honoured
        without every caller having to check it.
        """
        from context.state import settings

        wanted = settings.current().save_prompt
        if wanted == "never" or wanted != moment:
            return False

        current = active_context(self.store.contexts, backend=self.backend)
        if current is None or (leaving is not None and current.id == leaving.id):
            return False
        if not has_drifted(current, backend=self.backend):
            return False
        self._ask_to_save(current)
        return True

    def _ask_to_save(self, ctx: Context) -> None:
        if ctx.id in self.asked_about:
            return
        sent = notify.send(
            self,
            "drift",
            f"“{ctx.title}” has changed",
            "Its windows no longer match what was saved.",
            button="Save layout",
            on_click=lambda: self._save_drift(ctx),
        )
        # Once per context per run — but only once *asked*. With notifications
        # switched off the send is a no-op, and marking the context asked
        # anyway consumed the prompt without it ever appearing: turning
        # notifications back on then never offered to save.
        if sent:
            self.asked_about.add(ctx.id)

    def _save_drift(self, ctx: Context) -> None:
        self.save_context(ctx)

    def _focus_active_context(self) -> None:
        """Switch to whichever context is open, so a relaunch lands somewhere."""
        switch = getattr(self.backend, "switch_to", None)
        if switch is None:
            return
        for ctx in self.store.contexts:
            handle = ctx.handle_for(self.backend.name)
            if handle and self.backend.workspace_exists(handle):
                self.log.info("switching to open context %s", ctx.title)
                switch(Workspace(handle=handle, label=ctx.title))
                return

    def do_activate(self) -> None:
        if self.window is None:
            # Adopt anything still running from a previous run before the list
            # is built, so a restart does not offer to relaunch what is open.
            live = reconnect(self.store.contexts, backend=self.backend)
            if live:
                self.log.info("reconnected to %d running context(s)", len(live))
            self.store.save()

            self._build_launchers()
            self._watch_monitors()
            # Home, ready before anything asks to go there. After the launchers
            # so they are on screen first.
            GLib.idle_add(self._prepare_home_once)
        for launcher_window in self.launchers:
            launcher_window.present()
        # Nothing running is the one moment Context has nothing to show, so it
        # shows everything: the overview is what a desktop with no windows on
        # it is for.
        open_ids, _active = open_state(self.store.contexts, backend=self.backend)
        self.note_open_contexts(len(open_ids))

    def _prepare_home_once(self) -> bool:
        try:
            self.prepare_home()
        except Exception:
            # Home not being ready must not take the launcher down with it;
            # the next visit builds it the slow way.
            self.log.exception("could not prepare home")
        return GLib.SOURCE_REMOVE

    def _watch_monitors(self) -> None:
        """Rebuild the launchers when a monitor is plugged in or unplugged.

        `Gdk.Display.get_monitors()` is a `Gio.ListModel`, so `items-changed`
        covers both directions. Without this the launchers were built once at
        startup and never revisited: unplugging a screen left a launcher docked
        to an output that no longer existed — a second bar stacked on the
        remaining monitor — and plugging one in gave it no launcher at all.
        """
        display = Gdk.Display.get_default()
        if display is None:
            return
        self._monitor_model = display.get_monitors()
        self._monitor_model.connect("items-changed", self._on_monitors_changed)

    def _on_monitors_changed(self, model, position, removed, added) -> None:
        # Hot-plug arrives as a burst: a monitor being enabled can remove and
        # re-add itself as modes settle. Rebuilding on each one would tear the
        # launchers down mid-change, so settle first and rebuild once.
        self.log.info("monitors changed (+%d -%d)", added, removed)
        if self._monitor_settle:
            GLib.source_remove(self._monitor_settle)
        self._monitor_settle = GLib.timeout_add(MONITOR_SETTLE_MS, self._remonitor)

    def _remonitor(self) -> bool:
        self._monitor_settle = 0
        self.rebuild_launchers()
        return GLib.SOURCE_REMOVE

    def rebuild_launchers(self) -> None:
        """Put the launchers back on the screens they now belong on.

        Also the way the monitor setting takes effect without a restart: going
        from one screen to every screen is the same operation as a cable being
        plugged in.
        """
        from context.system import monitors

        wanted = [getattr(m, "name", None) for m in monitors.docks_on(self.backend)]
        if wanted == [w.monitor for w in self.launchers]:
            return

        for window in self.launchers:
            window.destroy()
        self.window = None
        self.extra_windows = []

        self._build_launchers()
        for window in self.launchers:
            window.present()

    @property
    def launchers(self) -> list[LauncherWindow]:
        return ([self.window] if self.window is not None else []) + self.extra_windows

    def _build_launchers(self) -> None:
        """One launcher per screen it should dock to.

        A layer surface belongs to exactly one output, so showing the launcher
        everywhere means a window each rather than one that spans. They share
        the store, so they show the same contexts and stay in step through
        `refresh_all`.
        """
        from context.system import monitors

        docks = monitors.docks_on(self.backend)
        for index, monitor in enumerate(docks):
            window = LauncherWindow(
                self,
                self.store,
                self.launch_context,
                self.close_context,
                monitor=getattr(monitor, "name", None),
                notes=self.notes,
            )
            if index == 0:
                self.window = window
            else:
                self.extra_windows.append(window)
        self.log.info("launcher on %d screen(s)", len(docks))

    def toggle_collapsed(self) -> None:
        window = self.ensure_window()
        if window is not None:
            window.toggle_collapsed()

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse or expand every launcher together.

        There is one stored collapsed state, so the launchers cannot disagree
        about it without one of them being wrong after a restart.
        """
        uistate.save(collapsed=collapsed)
        for launcher_window in self.launchers:
            launcher_window.set_collapsed(collapsed)

    def refresh_all(self) -> None:
        """Keep every launcher — and home — showing the same thing.

        The overview is on screen whenever you are standing on it, so it has to
        be told as well: as an overlay it was rebuilt each time it appeared and
        could not be out of date.
        """
        for launcher_window in self.launchers:
            launcher_window.refresh_open_state()
        if self.overview is not None:
            self.overview.refresh()

    def launch_context(self, ctx: Context) -> None:
        """Start a context on a worker thread.

        Launching is unavoidably slow: applications are started one at a time
        and each is waited for, so the compositor tiles it before the next one
        opens. Doing that on the main loop froze the whole launcher for the
        duration — and an application that never exits froze it for good.
        Nothing here may touch GTK; the result goes back through `idle_add`.
        """
        if ctx.id in self.launching:
            self.log.info("%s is already being launched", ctx.title)
            return
        self.launching.add(ctx.id)
        threading.Thread(
            target=self._launch_worker, args=(ctx,), daemon=True
        ).start()

    def _launch_worker(self, ctx: Context) -> None:
        try:
            result = launch_ctx(ctx, backend=self.backend)
        except Exception:
            # A launch must never leave the context wedged as in-flight, so even
            # an unexpected failure has to reach the main loop.
            self.log.exception("launching %s failed", ctx.title)
            result = None
        GLib.idle_add(self._launch_finished, ctx, result)

    def _launch_finished(self, ctx: Context, result) -> bool:
        self.launching.discard(ctx.id)
        if result is None:
            return False
        # launch_context records the workspace handle, and may have repaired the
        # layout, so both are written back.
        self.store.save()
        if result.layout_repaired:
            self.log.info("repaired the layout for %s for this launch", ctx.title)
        self.log.info(
            "launched %s: workspace=%s launched=%d failed=%d",
            ctx.title, result.workspace, len(result.launched), len(result.failed),
        )
        for app_id, error in result.failed:
            self.log.error("could not launch %s: %s", app_id, error)
        if self.window is not None:
            self.window.report_launch(ctx, result)
        self.refresh_all()
        return False

    def focus_loose(self, ctx: Context) -> None:
        windows = getattr(ctx, "windows", [])
        if not windows:
            return
        self.backend.focus_window(str(windows[0].get("id") or ""), warp=False)

    def save_context(self, ctx: Context) -> None:
        """Keep a context's windows as they are.

        For the no-context that means becoming a context: its windows are
        gathered into one workspace, captured from there, and the editor opens
        so it can be named. For a real one it is what the drift prompt does.
        """
        if is_no_context(ctx):
            self.save_loose(ctx)
            return
        windows, screens = capture_arrangement(ctx, backend=self.backend)
        self.store.save()
        self.asked_about.discard(ctx.id)
        notify.withdraw(self, "drift")
        message = (
            f"Saved {windows} window{'s' if windows != 1 else ''} for “{ctx.title}”"
            if windows
            else f"Nothing open to save for “{ctx.title}”"
        )
        self.log.info("saved %s: %d window(s), %d screen(s)", ctx.title, windows, screens)
        notify.send(self, "capture", ctx.title, message)
        self.refresh_all()

    def save_loose(self, ctx: Context) -> None:
        """Turn the windows that belong nowhere into a context that owns them."""
        windows = getattr(ctx, "windows", [])
        if not windows:
            return
        adopted = self.store.create(
            "New context",
            resources=[Resource(app_id=w["app_id"]) for w in windows if w.get("app_id")],
        )
        moved = adopt_loose(adopted, windows, backend=self.backend)
        self.store.save()
        self.log.info("adopted %d loose window(s) into a new context", moved)
        notify.send(
            self,
            "adopt",
            "Windows gathered",
            f"{moved} window{'s' if moved != 1 else ''} are now a context — name it",
        )
        self.refresh_all()
        # Named last: the windows are already where they belong, so backing out
        # of the editor leaves a saved context rather than undoing the move.
        self.edit_context(adopted)

    def close_context(self, ctx: Context) -> None:
        if is_no_context(ctx):
            closed = close_loose(getattr(ctx, "windows", []), backend=self.backend)
            self.log.info("closed %d window(s) belonging to no context", closed)
            notify.send(
                self,
                "close",
                "Closed",
                f"{closed} window{'s' if closed != 1 else ''} in no context",
            )
            self.refresh_all()
            return
        # Before closing, while there is still something to read.
        self.offer_to_save("close")
        result = close_ctx(ctx, backend=self.backend)
        # close_context may drop the workspace handle.
        self.store.save()
        self.log.info("closed %d window(s) for %s", result.closed, ctx.title)
        if self.window is not None:
            self.window.report_close(ctx, result)
        self.refresh_all()


def main(argv: list[str] | None = None) -> int:
    return ContextApplication().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())

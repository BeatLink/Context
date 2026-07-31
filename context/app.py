"""Application entrypoint for the Context launcher."""

from __future__ import annotations

import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from . import backends, switcher, uistate, widgets
from .backends import Workspace
from .launcher import active_context, capture_arrangement, has_drifted
from .launcher import move_window_to_context, move_window_to_screen
from .launcher import unmanaged_windows
from .launcher import close_context as close_ctx
from .launcher import launch_context as launch_ctx
from .launcher import reconnect
from .store import Context, ContextStore
from .logging_setup import configure, get_logger
from .window import LauncherWindow


# How long to wait for monitor hot-plug to settle before rebuilding. Enabling a
# screen emits several changes as modes are negotiated; rebuilding on each one
# tears the launchers down mid-change.
MONITOR_SETTLE_MS = 400


# Commands a keybind can send to the running instance. `python3 -m context
# switch-window` hands its command line over D-Bus rather than starting a
# second copy, so these cost nothing to bind.
COMMANDS = {
    "switch": lambda app: app.switch_context(),
    "switch-window": lambda app: app.switch_window(),
    "switch-window-all": lambda app: app.switch_window_all(),
    "previous": lambda app: app.previous_context(),
    "settings": lambda app: app.ensure_window().open_settings(),
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
        self.backend = backends.detect()
        self.log.info("backend: %s", self.backend.name)
        # The primary launcher, and the extras when it is shown on every
        # screen. `window` stays the one everything else talks to — toasts and
        # refreshes go to it — while `windows` is what gets kept in step.
        self.window: LauncherWindow | None = None
        self.extra_windows: list[LauncherWindow] = []
        # Contexts already offered a save this run, so a drifting one that is
        # left unsaved does not ask again at every opportunity.
        self.asked_about: set[str] = set()
        self.launching: set[str] = set()
        self.switcher: switcher.SwitcherWindow | None = None
        # Held so the monitor list is not collected while it is being watched.
        self._monitor_model = None
        self._monitor_settle = 0

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
        return False

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
        # not told, and the launcher would come back beside its own ghost.
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
        elif self.window is not None:
            self.window.toasts.add_toast(
                widgets.Toast(title=f"Open “{ctx.title}” first", timeout=4)
            )

    def adopt_windows(self) -> None:
        """Offer every window that belongs to no context a home."""
        loose = unmanaged_windows(self.store.contexts, backend=self.backend)
        if not loose:
            self.log.info("every window already belongs to a context")
            if self.window is not None:
                self.window.toasts.add_toast(
                    widgets.Toast(title="Every window is in a context", timeout=3)
                )
            return
        from .adopt import AdoptWindow

        picker = AdoptWindow(self, self.store, loose, self.backend)
        self._show_picker(picker)

    def capture_context(self) -> None:
        """Save what the current context has become."""
        current = active_context(self.store.contexts, backend=self.backend)
        if current is None:
            self.log.info("not in a context")
            return
        windows, screens = capture_arrangement(current, backend=self.backend)
        self.store.save()
        self.log.info(
            "captured %s: %d window(s) across %d screen(s)",
            current.title, windows, screens,
        )
        if self.window is not None:
            message = (
                f"Saved {windows} window{'s' if windows != 1 else ''} for "
                f"“{current.title}”"
                if windows
                else f"Nothing open to save for “{current.title}”"
            )
            self.window.toasts.add_toast(widgets.Toast(title=message, timeout=3))
        self.refresh_all()

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
        from . import settings

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
        if self.window is None or ctx.id in self.asked_about:
            return
        # Once per context per run: a context that drifts and is not saved
        # would otherwise ask again on every switch.
        self.asked_about.add(ctx.id)

        toast = widgets.Toast(title=f"“{ctx.title}” has changed", timeout=8)
        toast.set_button_label("Save layout")
        toast.connect("button-clicked", lambda _t: self._save_drift(ctx))
        self.window.toasts.add_toast(toast)

    def _save_drift(self, ctx: Context) -> None:
        windows, screens = capture_arrangement(ctx, backend=self.backend)
        self.store.save()
        self.asked_about.discard(ctx.id)
        self.log.info(
            "saved %s: %d window(s) across %d screen(s)", ctx.title, windows, screens
        )
        self.refresh_all()

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
        for launcher_window in self.launchers:
            launcher_window.present()

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
        from . import monitors

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
        from . import monitors

        docks = monitors.docks_on(self.backend)
        for index, monitor in enumerate(docks):
            window = LauncherWindow(
                self,
                self.store,
                self.launch_context,
                self.close_context,
                monitor=getattr(monitor, "name", None),
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
        """Keep every launcher showing the same thing."""
        for launcher_window in self.launchers:
            launcher_window.refresh_open_state()

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
            self.log.info("repaired the layout for %s", ctx.title)
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

    def close_context(self, ctx: Context) -> None:
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

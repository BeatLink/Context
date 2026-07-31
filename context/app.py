"""Application entrypoint for the Context launcher."""

from __future__ import annotations

import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib

from . import backends, switcher, uistate
from .backends import Workspace
from .launcher import active_context
from .launcher import close_context as close_ctx
from .launcher import launch_context as launch_ctx
from .launcher import reconnect
from .store import Context, ContextStore
from .logging_setup import configure, get_logger
from .window import LauncherWindow


# Commands a keybind can send to the running instance. `python3 -m context
# switch-window` hands its command line over D-Bus rather than starting a
# second copy, so these cost nothing to bind.
COMMANDS = {
    "switch": lambda app: app.switch_context(),
    "switch-window": lambda app: app.switch_window(),
    "switch-window-all": lambda app: app.switch_window_all(),
    "previous": lambda app: app.previous_context(),
    "settings": lambda app: app.ensure_window().open_settings(),
    "toggle-rail": lambda app: app.ensure_window().toggle_collapsed(),
    "restart": lambda app: app.restart(),
}


class ContextApplication(Adw.Application):
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
        self.window: LauncherWindow | None = None
        self.launching: set[str] = set()
        self.switcher: switcher.SwitcherWindow | None = None

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
        uistate.note_visit(ctx.id)
        self.launch_context(ctx)

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

            self.window = LauncherWindow(
                self, self.store, self.launch_context, self.close_context
            )
        self.window.present()

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
            self.window.refresh_open_state()
        return False

    def close_context(self, ctx: Context) -> None:
        result = close_ctx(ctx, backend=self.backend)
        # close_context may drop the workspace handle.
        self.store.save()
        self.log.info("closed %d window(s) for %s", result.closed, ctx.title)
        if self.window is not None:
            self.window.report_close(ctx, result)


def main(argv: list[str] | None = None) -> int:
    return ContextApplication().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())

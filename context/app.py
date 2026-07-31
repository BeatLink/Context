"""Application entrypoint for the Context launcher."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

from . import backends
from .backends import Workspace
from .launcher import close_context as close_ctx
from .launcher import launch_context as launch_ctx
from .launcher import reconnect
from .store import Context, ContextStore
from .logging_setup import configure, get_logger
from .window import LauncherWindow


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

    def do_command_line(self, command_line) -> int:
        """Entry point for every launch, first or subsequent.

        GApplication already enforces a single instance — a second launch hands
        its command line to the first over D-Bus and exits. That was previously
        silent, which made a stale instance look like a broken build: relaunches
        appeared to do nothing. Now the running instance says so and focuses the
        context in view, and the launcher is presented rather than duplicated.
        """
        if self.window is not None:
            self.log.info("already running; focusing the existing launcher")
            self._focus_active_context()
        self.activate()
        return 0

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
        result = launch_ctx(ctx, backend=self.backend)
        # launch_context records the workspace handle on the context.
        self.store.save()
        self.log.info(
            "launched %s: workspace=%s launched=%d failed=%d",
            ctx.title, result.workspace, len(result.launched), len(result.failed),
        )
        for app_id, error in result.failed:
            self.log.error("could not launch %s: %s", app_id, error)
        if self.window is not None:
            self.window.report_launch(ctx, result)

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

"""Application entrypoint for the Context launcher."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw

from . import backends
from .launcher import close_context as close_ctx
from .launcher import launch_context as launch_ctx
from .store import Context, ContextStore
from .logging_setup import configure, get_logger
from .watcher import WorkspaceWatcher
from .window import LauncherWindow


class ContextApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.beatlink.Context")
        configure()
        self.log = get_logger("app")
        self.store = ContextStore()
        self.backend = backends.detect()
        # Context owns its workspaces: anything opening on one must float, since
        # Context places windows itself rather than letting the compositor tile.
        self.watcher = WorkspaceWatcher(self.backend)
        self.log.info("backend: %s", self.backend.name)
        self.window: LauncherWindow | None = None

    def do_shutdown(self) -> None:
        self.watcher.stop()
        Adw.Application.do_shutdown(self)

    def do_activate(self) -> None:
        # Started here rather than in do_startup: the watcher only matters once
        # there is a session to manage, and starting it early raced the socket.
        if not self.watcher.running and hasattr(self.backend, "float_window"):
            if not self.watcher.start():
                self.log.warning("watcher unavailable; windows opened later will tile")

        if self.window is None:
            self.window = LauncherWindow(
                self, self.store, self.launch_context, self.close_context
            )
        self.window.present()

    def launch_context(self, ctx: Context) -> None:
        result = launch_ctx(ctx, backend=self.backend)
        # launch_context records the workspace handle on the context.
        self.store.save()
        self.log.info(
            "launched %s: workspace=%s launched=%d failed=%d placed=%d",
            ctx.title, result.workspace, len(result.launched), len(result.failed),
            result.placed,
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

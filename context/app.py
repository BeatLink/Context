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
from .window import LauncherWindow


class ContextApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.beatlink.Context")
        self.store = ContextStore()
        self.backend = backends.detect()
        self.window: LauncherWindow | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = LauncherWindow(
                self, self.store, self.launch_context, self.close_context
            )
        self.window.present()

    def launch_context(self, ctx: Context) -> None:
        result = launch_ctx(ctx, backend=self.backend)
        # launch_context records the workspace handle on the context.
        self.store.save()
        for app_id in result.launched:
            print(f"launched {app_id}", flush=True)
        for app_id, error in result.failed:
            print(f"failed {app_id}: {error}", file=sys.stderr, flush=True)
        if self.window is not None:
            self.window.report_launch(ctx, result)

    def close_context(self, ctx: Context) -> None:
        result = close_ctx(ctx, backend=self.backend)
        # close_context may drop the workspace handle.
        self.store.save()
        print(f"closed {result.closed} window(s) for {ctx.title}", flush=True)
        if self.window is not None:
            self.window.report_close(ctx, result)


def main(argv: list[str] | None = None) -> int:
    return ContextApplication().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())

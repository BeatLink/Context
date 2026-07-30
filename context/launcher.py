"""Instantiating a context: placing it in a workspace and launching its apps."""

from __future__ import annotations

from dataclasses import dataclass, field

from gi.repository import Gio, GLib

from . import backends
from .backends import Backend, Workspace
from .store import Context


@dataclass
class LaunchResult:
    launched: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    backend: str = "none"
    workspace: str | None = None
    reused_workspace: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed


def launch_app(app_id: str) -> None:
    try:
        info = Gio.DesktopAppInfo.new(app_id)
    except TypeError as exc:
        raise LookupError(f"no desktop entry for {app_id}") from exc
    if info is None:
        raise LookupError(f"no desktop entry for {app_id}")
    info.launch([], Gio.AppLaunchContext())


def _launch_apps(ctx: Context) -> tuple[list[str], list[tuple[str, str]]]:
    launched: list[str] = []
    failed: list[tuple[str, str]] = []
    for app_id in ctx.apps:
        try:
            launch_app(app_id)
            launched.append(app_id)
        except (GLib.Error, LookupError) as exc:
            failed.append((app_id, str(exc)))
    return launched, failed


def launch_context(
    ctx: Context,
    backend: Backend | None = None,
    use_workspaces: bool = True,
) -> LaunchResult:
    wm: Backend = backend or (backends.detect() if use_workspaces else backends.NullBackend())
    result = LaunchResult(backend=wm.name)

    workspace: Workspace | None = None
    if use_workspaces:
        workspace = wm.ensure_workspace(ctx.title, ctx.handle_for(wm.name))

    if workspace is not None:
        ctx.set_handle(wm.name, workspace.handle)
        result.workspace = workspace.handle
        wm.switch_to(workspace)
        wm.prepare_launch(workspace)

        if not workspace.created:
            # The workspace is already populated; going there is the whole job.
            result.reused_workspace = True
            return result

    result.launched, result.failed = _launch_apps(ctx)
    return result

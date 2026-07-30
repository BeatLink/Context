"""Hyprland workspaces as context containers.

Hyprland supports *named* workspaces, so a context's handle is its workspace name
rather than a positional index. Names are stable across reordering, and
`dispatch workspace name:<x>` both creates and focuses one, so ensure/switch
collapse into a single operation.

Because a named workspace only exists once it holds a window, existence is
determined by querying `hyprctl workspaces`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from .base import Workspace

HANDLE_PREFIX = "ctx-"


def _sanitize(title: str) -> str:
    kept = [c if (c.isalnum() or c in "-_") else "-" for c in title.strip().casefold()]
    slug = "".join(kept).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "untitled"


class HyprlandBackend:
    name = "hyprland"

    def available(self) -> bool:
        if shutil.which("hyprctl") is None:
            return False
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            return False
        return self._query("version") is not None

    def _run(self, *args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                ["hyprctl", *args], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def _query(self, *args: str):
        result = self._run("-j", *args)
        if result is None or result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def workspace_names(self) -> list[str]:
        data = self._query("workspaces")
        if not isinstance(data, list):
            return []
        return [str(w.get("name", "")) for w in data if isinstance(w, dict)]

    def current_handle(self) -> str | None:
        data = self._query("activeworkspace")
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        return str(name) if name else None

    def ensure_workspace(self, title: str, handle: str | None) -> Workspace | None:
        name = handle or f"{HANDLE_PREFIX}{_sanitize(title)}"
        exists = name in self.workspace_names()
        return Workspace(handle=name, label=title, created=not exists)

    def switch_to(self, workspace: Workspace) -> bool:
        result = self._run("dispatch", "workspace", f"name:{workspace.handle}")
        return result is not None and result.returncode == 0

    def prepare_launch(self, workspace: Workspace) -> None:
        # Focusing the workspace is enough: Hyprland places new windows on the
        # active workspace. Switching happens before launch in the launcher.
        return None

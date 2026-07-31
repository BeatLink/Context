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

    def workspace_exists(self, handle: str) -> bool:
        return handle in self.workspace_names()

    def _windows_on(self, handle: str) -> list[str]:
        data = self._query("clients")
        if not isinstance(data, list):
            return []
        addresses = []
        for client in data:
            if not isinstance(client, dict):
                continue
            workspace = client.get("workspace") or {}
            if str(workspace.get("name", "")) != handle:
                continue
            address = client.get("address")
            if address:
                addresses.append(str(address))
        return addresses

    def window_count(self, handle: str) -> int:
        return len(self._windows_on(handle))

    def close_workspace(self, handle: str) -> int:
        closed = 0
        for address in self._windows_on(handle):
            result = self._run("dispatch", "closewindow", f"address:{address}")
            if result is not None and result.returncode == 0:
                closed += 1
        return closed

    def apply_layout(self, handle: str, slots) -> int:
        """Place the workspace's windows into `slots`, in the order they appear.

        Windows are floated and positioned explicitly rather than tiled: the
        layout describes exact rectangles, which the dwindle layout cannot honour.
        Returns how many windows were placed.
        """
        if not slots:
            return 0

        monitor = self._focused_monitor()
        if monitor is None:
            return 0
        mon_w, mon_h = monitor

        placed = 0
        for address, slot in zip(self._windows_on(handle), slots):
            target = f"address:{address}"
            x = int(round(slot.x * mon_w))
            y = int(round(slot.y * mon_h))
            w = max(80, int(round(slot.width * mon_w)))
            h = max(60, int(round(slot.height * mon_h)))
            result = self._run(
                "--batch",
                f"dispatch setfloating {target} ; "
                f"dispatch resizewindowpixel exact {w} {h},{target} ; "
                f"dispatch movewindowpixel exact {x} {y},{target}",
            )
            if result is not None and result.returncode == 0:
                placed += 1
        return placed

    def _focused_monitor(self) -> tuple[int, int] | None:
        data = self._query("monitors")
        if not isinstance(data, list):
            return None
        for monitor in data:
            if isinstance(monitor, dict) and monitor.get("focused"):
                try:
                    return int(monitor["width"]), int(monitor["height"])
                except (KeyError, TypeError, ValueError):
                    return None
        return None

    def remove_workspace(self, handle: str) -> bool:
        # Named workspaces disappear on their own once the last window closes,
        # and the handle stays valid because it is a name, not a position.
        return not self.workspace_exists(handle)

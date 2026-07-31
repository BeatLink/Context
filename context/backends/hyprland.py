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
        # Float whatever is already here. Windows that open later are floated by
        # WorkspaceWatcher, since Hyprland has no per-workspace float rule.
        self.claim_workspace(workspace.handle)

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

    def float_window(self, address: str) -> bool:
        """Float one window, by address."""
        result = self._run("dispatch", "setfloating", f"address:{address}")
        return result is not None and result.returncode == 0

    def claim_workspace(self, handle: str) -> int:
        """Float everything currently on a context's workspace.

        Hyprland cannot express "this workspace does not tile": workspace rules
        have no float field, and a `match:workspace` window rule is accepted but
        never applied. Windows opened later are handled by WorkspaceWatcher; this
        covers the ones already there.
        """
        return sum(1 for a in self._windows_on(handle) if self.float_window(a))

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
        origin_x, origin_y, mon_w, mon_h = monitor

        # Re-assert ownership over anything that arrived since the launch.
        self.claim_workspace(handle)

        placed = 0
        for address, slot in zip(self._windows_on(handle), slots):
            target = f"address:{address}"
            x = origin_x + int(round(slot.x * mon_w))
            y = origin_y + int(round(slot.y * mon_h))
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

    def _focused_monitor(self) -> tuple[int, int, int, int] | None:
        """The usable area of the focused monitor as (x, y, width, height).

        `reserved` is the space claimed by layer-shell surfaces — the bars, and
        Context's own sidebar. Laying windows out over the full monitor would put
        them underneath those, so slots are mapped into what is left.
        """
        data = self._query("monitors")
        if not isinstance(data, list):
            return None
        for monitor in data:
            if not (isinstance(monitor, dict) and monitor.get("focused")):
                continue
            try:
                width = int(monitor["width"])
                height = int(monitor["height"])
            except (KeyError, TypeError, ValueError):
                return None

            reserved = monitor.get("reserved") or [0, 0, 0, 0]
            try:
                left, top, right, bottom = (int(v) for v in reserved[:4])
            except (TypeError, ValueError):
                left = top = right = bottom = 0

            usable_w = max(1, width - left - right)
            usable_h = max(1, height - top - bottom)
            return left, top, usable_w, usable_h
        return None

    def remove_workspace(self, handle: str) -> bool:
        # Named workspaces disappear on their own once the last window closes,
        # and the handle stays valid because it is a name, not a position.
        return not self.workspace_exists(handle)

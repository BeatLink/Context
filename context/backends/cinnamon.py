"""Cinnamon workspaces as context containers.

Workspaces are addressed by index. The index is stored on the context as its
handle, so renaming a context relabels its existing workspace instead of
orphaning it.
"""

from __future__ import annotations

import shutil
import subprocess

from gi.repository import Gio

from .base import Workspace

WM_SCHEMA = "org.cinnamon.desktop.wm.preferences"
NAMES_KEY = "workspace-names"
NUM_KEY = "num-workspaces"
MAX_WORKSPACES = 36


class CinnamonBackend:
    name = "cinnamon"

    def available(self) -> bool:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup(WM_SCHEMA, True) is None:
            return False
        return shutil.which("wmctrl") is not None

    def _settings(self) -> Gio.Settings:
        return Gio.Settings.new(WM_SCHEMA)

    def workspace_names(self) -> list[str]:
        return list(self._settings().get_strv(NAMES_KEY))

    def workspace_count(self) -> int:
        return self._settings().get_int(NUM_KEY)

    def current_handle(self) -> str | None:
        result = subprocess.run(["wmctrl", "-d"], capture_output=True, text=True)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "*":
                return parts[0]
        return None

    def _set_label(self, index: int, label: str) -> None:
        settings = self._settings()
        names = list(settings.get_strv(NAMES_KEY))
        while len(names) <= index:
            names.append(f"Workspace {len(names) + 1}")
        names[index] = label
        settings.set_strv(NAMES_KEY, names)
        Gio.Settings.sync()

    def _find_by_label(self, label: str) -> int | None:
        target = label.strip().casefold()
        count = self.workspace_count()
        for index, existing in enumerate(self.workspace_names()):
            if existing.strip().casefold() == target and index < count:
                return index
        return None

    def _create(self, label: str) -> int | None:
        settings = self._settings()
        count = settings.get_int(NUM_KEY)
        if count >= MAX_WORKSPACES:
            return None
        index = count
        settings.set_int(NUM_KEY, count + 1)
        Gio.Settings.sync()
        self._set_label(index, label)
        return index

    def ensure_workspace(self, title: str, handle: str | None) -> Workspace | None:
        if handle is not None and handle.isdigit():
            index = int(handle)
            if index < self.workspace_count():
                # Keep the label in step with a renamed context.
                if self.workspace_names()[index : index + 1] != [title]:
                    self._set_label(index, title)
                return Workspace(handle=handle, label=title, created=False)

        existing = self._find_by_label(title)
        if existing is not None:
            return Workspace(handle=str(existing), label=title, created=False)

        index = self._create(title)
        if index is None:
            return None
        return Workspace(handle=str(index), label=title, created=True)

    def switch_to(self, workspace: Workspace) -> bool:
        return subprocess.run(
            ["wmctrl", "-s", workspace.handle], capture_output=True, text=True
        ).returncode == 0

    def prepare_launch(self, workspace: Workspace) -> None:
        return None

    def workspace_exists(self, handle: str) -> bool:
        return handle.isdigit() and int(handle) < self.workspace_count()

    def _windows_on(self, handle: str) -> list[str]:
        """Window ids on this workspace only.

        Windows report their desktop in field 2. Sticky windows (-1) appear on
        every workspace and are never owned by a context, so they are excluded.
        """
        if not handle.isdigit():
            return []
        result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            return []
        windows = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 3:
                continue
            window_id, desktop = parts[0], parts[1]
            if desktop == handle and desktop != "-1":
                windows.append(window_id)
        return windows

    def window_count(self, handle: str) -> int:
        if not self.workspace_exists(handle):
            return 0
        return len(self._windows_on(handle))

    def close_workspace(self, handle: str) -> int:
        closed = 0
        for window_id in self._windows_on(handle):
            result = subprocess.run(
                ["wmctrl", "-i", "-c", window_id], capture_output=True, text=True
            )
            if result.returncode == 0:
                closed += 1
        return closed

    def remove_workspace(self, handle: str) -> bool:
        """Drop the workspace, but only when it is the last one.

        `num-workspaces` is a count, so lowering it always removes from the end.
        Removing a workspace from the middle would renumber every workspace after
        it, silently repointing other contexts' handles at the wrong workspace.
        """
        if not handle.isdigit():
            return False
        index = int(handle)
        count = self.workspace_count()
        if index != count - 1 or count <= 1:
            return False
        if self.window_count(handle):
            return False

        settings = self._settings()
        names = list(settings.get_strv(NAMES_KEY))
        if len(names) > index:
            del names[index:]
            settings.set_strv(NAMES_KEY, names)
        settings.set_int(NUM_KEY, count - 1)
        Gio.Settings.sync()
        return True

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

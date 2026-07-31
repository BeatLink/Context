"""Terminals, opened at a directory and optionally running a command.

Tilix is D-Bus activated and single-instance by default: plain `tilix` hands the
request to the running process, which raises its existing window rather than
making one. `--action=app-new-window` is what forces a new one, and without it a
context that includes a terminal silently gets no terminal.

Other terminals differ, so the flag is per-binary rather than assumed, and the
resource's own `force_new_window` can turn it off for the cases where insisting
on a new window is wrong.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..logging_setup import get_logger, traced
from ..resources import Resource
from .base import child_env

log = get_logger("adapter.terminal")

APP_IDS = {
    "com.gexperts.tilix.desktop",
    "tilix.desktop",
    "org.gnome.terminal.desktop",
    "foot.desktop",
    "kitty.desktop",
    "alacritty.desktop",
}

# binary -> (working-directory flag, command flag, new-window argument)
#
# The new-window argument is empty where a terminal already makes one per
# invocation; only the single-instance ones need to be told.
TERMINALS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "tilix": ("--working-directory", "--command", ("--action=app-new-window",)),
    "gnome-terminal": ("--working-directory", "--", ("--window",)),
    "foot": ("--working-directory", "", ()),
    "kitty": ("--directory", "", ()),
    "alacritty": ("--working-directory", "--command", ()),
}


class TerminalAdapter:
    name = "terminal"

    def handles(self, resource: Resource) -> bool:
        return resource.app_id.strip().casefold() in APP_IDS

    def executable(self) -> str | None:
        for binary in TERMINALS:
            found = shutil.which(binary)
            if found:
                return found
        return None

    @traced(log)
    def launch(self, resource: Resource, context_id: str) -> None:
        binary = self.executable()
        if binary is None:
            raise LookupError("no terminal found")

        name = Path(binary).name
        cwd_flag, command_flag, new_window = TERMINALS.get(name, ("", "", ()))

        command = [binary]
        if resource.opens_its_own_window:
            command.extend(new_window)

        if resource.path:
            target = Path(resource.path).expanduser()
            if not target.is_dir():
                raise LookupError(f"{target} is not a directory")
            if cwd_flag:
                command.extend([cwd_flag, str(target)])

        # A command has to come last: most terminals treat everything after the
        # flag as the command line to run.
        if resource.command and command_flag:
            command.extend([command_flag, resource.command])

        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=child_env(),
            )
        except OSError as exc:
            raise LookupError(f"could not start {name}: {exc}") from exc

    def describe(self, resource: Resource) -> str:
        parts = []
        if resource.path:
            parts.append(Path(resource.path).name or resource.path)
        if resource.command:
            parts.append(f"running {resource.command}")
        return " · ".join(parts) if parts else "home directory"

    def teardown(self, resource: Resource, context_id: str) -> None:
        return None

"""VS Code and VSCodium, opened at a folder, a file, or a workspace.

The CLI takes paths positionally — `codium [paths...]` — and infers what to do
from what it is given: a directory opens as a folder, a `.code-workspace` file
opens as a multi-root workspace, anything else opens as a file. So a single
`path` on the resource covers all three, and the adapter only has to say which
one it is for the UI's benefit.

`--new-window` is passed so a context gets its own window rather than a tab in
whichever window happened to be focused, which is the same reasoning as the
Firefox adapter's separate profile.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..logging_setup import get_logger, traced
from ..resources import Resource
from .base import child_command, child_env

log = get_logger("adapter.vscode")

APP_IDS = {
    "codium.desktop",
    "code.desktop",
    "vscodium.desktop",
    "visual-studio-code.desktop",
    "com.visualstudio.code.desktop",
}

# In preference order: whichever is installed gets used.
BINARIES = ("codium", "code", "code-oss")

WORKSPACE_SUFFIX = ".code-workspace"


def target_kind(path: str | None) -> str:
    """What a path will open as: workspace, folder, file, or nothing."""
    if not path:
        return "none"
    candidate = Path(path).expanduser()
    if candidate.suffix == WORKSPACE_SUFFIX:
        return "workspace"
    if candidate.is_dir():
        return "folder"
    return "file"


class VSCodeAdapter:
    name = "vscode"

    def handles(self, resource: Resource) -> bool:
        return resource.app_id.strip().casefold() in APP_IDS

    def executable(self) -> str | None:
        for binary in BINARIES:
            found = shutil.which(binary)
            if found:
                return found
        return None

    @traced(log)
    def launch(self, resource: Resource, context_id: str) -> None:
        binary = self.executable()
        if binary is None:
            raise LookupError("no VS Code binary found")

        command = [binary, "--new-window"]
        if resource.path:
            target = str(Path(resource.path).expanduser())
            if not Path(target).exists():
                # Opening a missing path silently creates an empty window, which
                # looks like the context failing to restore.
                raise LookupError(f"{target} does not exist")
            command.append(target)

        try:
            subprocess.Popen(
                child_command(command),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=child_env(),
            )
        except OSError as exc:
            raise LookupError(f"could not start {binary}: {exc}") from exc

    def describe(self, resource: Resource) -> str:
        kind = target_kind(resource.path)
        if kind == "none":
            return "no folder yet"
        name = Path(resource.path).name or resource.path
        if kind == "workspace":
            return f"{name.removesuffix(WORKSPACE_SUFFIX)} (workspace)"
        return name

    def teardown(self, resource: Resource, context_id: str) -> None:
        return None

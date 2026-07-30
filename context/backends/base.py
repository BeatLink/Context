"""The window-manager backend interface.

A backend is responsible for the *container* a context lives in — creating it,
finding it again, and switching to it. Launching applications is the launcher's
job; a backend only says where they should land.

Contexts are identified by an opaque handle the backend chooses (a Cinnamon
workspace index, a Hyprland workspace name, …), stored on the context so renaming
it doesn't orphan the container.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Workspace:
    """A context's container, as the backend sees it."""

    handle: str
    label: str
    created: bool = False


@runtime_checkable
class Backend(Protocol):
    name: str

    def available(self) -> bool:
        """Whether this backend can drive the running session."""

    def ensure_workspace(self, title: str, handle: str | None) -> Workspace | None:
        """Find the workspace for `handle`, or create one labelled `title`.

        `created` on the result reports whether a new workspace was made, which
        tells the launcher whether apps still need starting.
        """

    def switch_to(self, workspace: Workspace) -> bool:
        """Focus a workspace. Returns whether the switch succeeded."""

    def current_handle(self) -> str | None:
        """The handle of the currently focused workspace, if known."""

    def prepare_launch(self, workspace: Workspace) -> None:
        """Hook run before apps launch, for backends that bind launches to a
        workspace rather than relying on focus."""


class NullBackend:
    """Used when no window manager can be driven: apps just launch here."""

    name = "none"

    def available(self) -> bool:
        return True

    def ensure_workspace(self, title: str, handle: str | None) -> Workspace | None:
        return None

    def switch_to(self, workspace: Workspace) -> bool:
        return False

    def current_handle(self) -> str | None:
        return None

    def prepare_launch(self, workspace: Workspace) -> None:
        return None

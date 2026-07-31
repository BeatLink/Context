"""The window-manager backend interface.

A backend is responsible for the *container* a context lives in — creating it,
finding it again, and switching to it. Launching applications is the launcher's
job; a backend only says where they should land.

Contexts are identified by an opaque handle the backend chooses (for Hyprland, a
workspace name), stored on the context so renaming it doesn't orphan the
container.
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


@dataclass(frozen=True)
class MonitorInfo:
    """One output, as much as a backend can say about it.

    `width` and `height` are in the compositor's logical pixels, already
    accounting for rotation — a portrait monitor reports the tall figures, not
    the panel's native landscape ones.
    """

    name: str
    width: int
    height: int
    x: int = 0
    y: int = 0
    scale: float = 1.0
    focused: bool = False
    # The compositor's own id. Windows report which monitor they are on by id
    # rather than by name, so matching one to the other needs this.
    id: int = -1

    @property
    def aspect(self) -> float:
        """Width over height, which is what a scale model needs."""
        return self.width / self.height if self.height else 16 / 9


@dataclass(frozen=True)
class WindowInfo:
    """One open window, as much as a backend can say about it.

    `app_id` is the compositor's class, which usually matches a desktop entry's
    basename and is how the window's icon is found. `handle` is the container
    it is on, so a switcher can say which context a window belongs to.
    """

    id: str
    title: str
    app_id: str
    handle: str | None = None


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

    def workspace_exists(self, handle: str) -> bool:
        """Whether the workspace for `handle` is still around."""

    def window_count(self, handle: str) -> int:
        """How many windows the workspace holds. -1 when unknown."""

    def live_handles(self) -> set[str]:
        """Every handle that currently holds at least one window.

        The launcher re-checks which contexts are open on a timer. Asking per
        context costs two queries each; this answers for all of them at once.
        """

    def windows(self, handle: str | None = None) -> list["WindowInfo"]:
        """Open windows, most recently focused first.

        `handle` limits the answer to one container; None means every window
        the backend can see.
        """

    def cursor_position(self) -> tuple[int, int] | None:
        """Where the pointer is in global coordinates, or None if unknown.

        Layer surfaces stop receiving pointer events the moment the cursor
        leaves them; the compositor still knows where it went.
        """

    def focus_window(self, window_id: str, warp: bool = True) -> bool:
        """Focus one window by its backend-specific id.

        `warp=False` asks for focus without the cursor following it, for the
        hand-backs that run while the pointer is mid-gesture somewhere else.
        """

    def monitors(self) -> list["MonitorInfo"]:
        """Every connected output. Empty when the backend cannot say."""

    def place_workspace(self, handle: str, monitor: str) -> bool:
        """Bind a workspace to an output, for contexts that span screens."""

    def move_window(self, window_id: str, handle: str) -> bool:
        """Send one window to another container, without following it."""

    def set_window_state(self, window_id: str, state: str) -> bool:
        """Fullscreen, maximise, restore, float, tile, pin or centre a window."""

    def swap_windows(self, window_id: str, direction: str) -> bool:
        """Swap a tiled window with its neighbour."""

    def group_windows(self, window_id: str, direction: str = "r") -> bool:
        """Fold a window into a tabbed group with its neighbour."""

    def ungroup_window(self, window_id: str) -> bool:
        """Take a window back out of its group."""

    def close_workspace(self, handle: str) -> int:
        """Ask every window on the workspace to close. Returns how many were
        asked. Must never touch windows outside this workspace."""

    def remove_workspace(self, handle: str) -> bool:
        """Discard the now-empty workspace itself, if the backend can do so
        without invalidating other contexts' handles."""


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

    def workspace_exists(self, handle: str) -> bool:
        return False

    def window_count(self, handle: str) -> int:
        return -1

    def live_handles(self) -> set[str]:
        return set()

    def windows(self, handle: str | None = None) -> list[WindowInfo]:
        return []

    def cursor_position(self) -> tuple[int, int] | None:
        return None

    def focus_window(self, window_id: str, warp: bool = True) -> bool:
        return False

    def monitors(self) -> list[MonitorInfo]:
        return []

    def place_workspace(self, handle: str, monitor: str) -> bool:
        return False

    def move_window(self, window_id: str, handle: str) -> bool:
        return False

    def set_window_state(self, window_id: str, state: str) -> bool:
        return False

    def swap_windows(self, window_id: str, direction: str) -> bool:
        return False

    def group_windows(self, window_id: str, direction: str = "r") -> bool:
        return False

    def ungroup_window(self, window_id: str) -> bool:
        return False

    def close_workspace(self, handle: str) -> int:
        return 0

    def remove_workspace(self, handle: str) -> bool:
        return False

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

from context.system.logging_setup import get_logger, traced
from .base import MonitorInfo, WindowInfo, Workspace

log = get_logger("backend.hyprland")

HANDLE_PREFIX = "ctx-"

# The overview's own workspace. Under the same prefix as a context's, so it is
# recognisably Context's, and a name rather than a number so it survives
# workspaces being reordered — the same reason contexts are named.
HOME_HANDLE = f"{HANDLE_PREFIX}home"


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
            result = subprocess.run(
                ["hyprctl", *args], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("hyprctl %s: %s", " ".join(args), exc)
            return None
        if result.returncode != 0:
            log.warning(
                "hyprctl %s exited %d: %s",
                " ".join(args), result.returncode, result.stderr.strip()[:120],
            )
        else:
            # Only the fact of the call: JSON queries return whole window lists,
            # which drown everything else in the log.
            log.debug("hyprctl %s ok", " ".join(args))
        return result

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

    @traced(log)
    def current_handle(self) -> str | None:
        data = self._query("activeworkspace")
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        return str(name) if name else None

    def home_handle(self) -> str | None:
        return HOME_HANDLE

    @traced(log)
    def bind_to_home(self, app_id: str, title: str) -> bool:
        """A window rule pinning the overview to home, whatever is current.

        `silent` so installing it — and rebuilding the window after a restart —
        does not drag you to home. Measured on 0.56: the window maps on
        `ctx-home` while the active workspace stays where it was.

        The 0.56 syntax is `match:` prefixes with a space before the value;
        `class:foo` is answered with "invalid field class:foo: missing a
        value". Check any change to this line with `hyprctl keyword`, which
        names the bad field rather than failing quietly.
        """
        if not app_id or not title:
            return False
        result = self._run(
            "keyword",
            "windowrule",
            f"workspace name:{HOME_HANDLE} silent, "
            f"match:class {app_id}, match:title {title}",
        )
        return result is not None and result.returncode == 0

    @traced(log)
    def ensure_workspace(self, title: str, handle: str | None) -> Workspace | None:
        name = handle or f"{HANDLE_PREFIX}{_sanitize(title)}"
        # A context called "Home" derives the overview's own handle, and the two
        # would then be the same workspace: opening it would put its apps on the
        # overview, and closing it would close the overview's window.
        if name == HOME_HANDLE and handle is None:
            name = f"{HOME_HANDLE}-1"
        exists = name in self.workspace_names()
        return Workspace(handle=name, label=title, created=not exists)

    @traced(log)
    def switch_to(self, workspace: Workspace) -> bool:
        result = self._run("dispatch", "workspace", f"name:{workspace.handle}")
        return result is not None and result.returncode == 0

    @traced(log)
    def place_workspace(self, handle: str, monitor: str) -> bool:
        """Bind a workspace to an output.

        Only works once the workspace exists — `moveworkspacetomonitor` answers
        "Workspace not found" for a name nothing has been opened on yet, so the
        caller has to switch to it first. Measured, not assumed.
        """
        if not handle or not monitor:
            return False
        result = self._run(
            "dispatch", "moveworkspacetomonitor", f"name:{handle} {monitor}"
        )
        return result is not None and result.returncode == 0

    def prepare_launch(self, workspace: Workspace) -> None:
        # Nothing to do: contexts tile, so the compositor places windows itself
        # and each launch only needs its split direction set beforehand.
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

    def live_handles(self) -> set[str]:
        data = self._query("clients")
        if not isinstance(data, list):
            return set()
        handles = set()
        for client in data:
            if not isinstance(client, dict):
                continue
            name = str((client.get("workspace") or {}).get("name", ""))
            if name:
                handles.add(name)
        return handles

    def windows(self, handle: str | None = None) -> list[WindowInfo]:
        """Open windows, most recently focused first.

        Hyprland gives each client a `focusHistoryID`, 0 being the focused
        window, so the switcher's ordering comes straight from the compositor
        rather than being tracked here.
        """
        data = self._query("clients")
        if not isinstance(data, list):
            return []

        found = []
        for client in data:
            if not isinstance(client, dict) or not client.get("address"):
                continue
            name = str((client.get("workspace") or {}).get("name", "")) or None
            if handle is not None and name != handle:
                continue
            try:
                order = int(client.get("focusHistoryID", 1 << 30))
            except (TypeError, ValueError):
                order = 1 << 30
            found.append(
                (
                    order,
                    WindowInfo(
                        id=str(client["address"]),
                        title=str(client.get("title") or ""),
                        app_id=str(client.get("class") or ""),
                        handle=name,
                    ),
                )
            )
        return [window for _, window in sorted(found, key=lambda pair: pair[0])]

    def monitors(self) -> list[MonitorInfo]:
        """Connected outputs, in the compositor's own logical coordinates.

        `width`/`height` already account for rotation, so a monitor turned on
        its side reports the tall figures — which is what a layout preview
        needs, and why the transform itself is not carried through.
        """
        data = self._query("monitors")
        if not isinstance(data, list):
            return []
        found = []
        for entry in data:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            try:
                found.append(
                    MonitorInfo(
                        name=str(entry["name"]),
                        width=int(entry.get("width", 0)),
                        height=int(entry.get("height", 0)),
                        x=int(entry.get("x", 0)),
                        y=int(entry.get("y", 0)),
                        scale=float(entry.get("scale", 1.0) or 1.0),
                        focused=bool(entry.get("focused", False)),
                        id=int(entry.get("id", -1)),
                    )
                )
            except (TypeError, ValueError):
                log.warning("skipping unreadable monitor %r", entry.get("name"))
        return found

    @traced(log)
    def cursor_position(self) -> tuple[int, int] | None:
        found = self._query("cursorpos")
        if not isinstance(found, dict):
            return None
        try:
            return int(found["x"]), int(found["y"])
        except (KeyError, TypeError, ValueError):
            return None

    def focus_window(self, window_id: str, warp: bool = True) -> bool:
        if not warp:
            # Focusing warps the cursor into the window unless cursor:no_warps
            # says otherwise — ruinous for the keyboard hand-back that runs as
            # the pointer leaves the sidebar, which yanked the cursor away
            # mid-gesture. The option is flipped around the dispatch and put
            # back, unless the user already has it on.
            option = self._query("getoption", "cursor:no_warps")
            if not (option and option.get("int")):
                result = self._run(
                    "--batch",
                    "keyword cursor:no_warps 1 ; "
                    f"dispatch focuswindow address:{window_id} ; "
                    "keyword cursor:no_warps 0",
                )
                return result is not None and result.returncode == 0
        result = self._run("dispatch", "focuswindow", f"address:{window_id}")
        return result is not None and result.returncode == 0

    @traced(log)
    def close_workspace(self, handle: str) -> int:
        closed = 0
        for address in self._windows_on(handle):
            if self.close_window(address):
                closed += 1
        return closed

    def close_window(self, window_id: str) -> bool:
        result = self._run("dispatch", "closewindow", f"address:{window_id}")
        return result is not None and result.returncode == 0

    def client_geometry(self, handle: str) -> list[dict]:
        """Where each window on a workspace actually is, in layout order.

        Sorted top-left to bottom-right rather than by focus, so capturing a
        context twice without moving anything gives the same answer.
        """
        return _in_layout_order(
            [g for g in map(self._geometry, self._clients_on(handle)) if g]
        )

    def geometry_by_handle(self) -> dict[str, list[dict]]:
        """The same, for every workspace at once.

        The launcher asks which contexts have drifted on every poll, and asking
        per context is one `hyprctl clients` each — enough subprocess work on
        the main loop to be felt with a handful of contexts open.
        """
        data = self._query("clients")
        if not isinstance(data, list):
            return {}
        grouped: dict[str, list[dict]] = {}
        for client in data:
            if not isinstance(client, dict) or not client.get("address"):
                continue
            handle = str((client.get("workspace") or {}).get("name", ""))
            geometry = self._geometry(client)
            if handle and geometry:
                grouped.setdefault(handle, []).append(geometry)
        return {handle: _in_layout_order(found) for handle, found in grouped.items()}

    def _geometry(self, client: dict) -> dict | None:
        try:
            x, y = int(client["at"][0]), int(client["at"][1])
            width, height = int(client["size"][0]), int(client["size"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return {
            # The address, so a window found this way can also be acted on.
            "id": str(client.get("address") or ""),
            "app_id": self._desktop_id(str(client.get("class") or "")),
            "title": str(client.get("title") or ""),
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            # `monitor` is an id, not a name — the launcher matches it against
            # MonitorInfo by position instead.
            "monitor_id": client.get("monitor"),
        }

    def _desktop_id(self, window_class: str) -> str:
        """A window's class as a desktop-entry id.

        The class usually *is* the basename, so this is mostly adding the
        suffix — but the entry is looked up to avoid inventing an id for a
        window whose class matches nothing installed.
        """
        if not window_class:
            return ""
        from gi.repository import Gio

        for candidate in (f"{window_class}.desktop", f"{window_class.lower()}.desktop"):
            try:
                if Gio.DesktopAppInfo.new(candidate) is not None:
                    return candidate
            except TypeError:
                continue
        return f"{window_class}.desktop"

    @traced(log)
    def move_window(self, window_id: str, handle: str) -> bool:
        """Send one window to another workspace without following it.

        `movetoworkspacesilent` rather than `movetoworkspace`: moving a window
        out of the context you are working in should not drag you out with it.
        """
        result = self._run(
            "dispatch",
            "movetoworkspacesilent",
            f"name:{handle},address:{window_id}",
        )
        return result is not None and result.returncode == 0

    @traced(log)
    def set_window_state(self, window_id: str, state: str) -> bool:
        """Fullscreen, float, tile or pin one window.

        Hyprland's state dispatchers act on the *active* window, so the window
        is focused first. That is a visible side effect, and the alternative —
        `--` address forms — is not offered for these.
        """
        dispatchers = {
            "fullscreen": ("fullscreen", "1"),
            "maximise": ("fullscreenstate", "0 2"),
            "restore": ("fullscreenstate", "0 0"),
            "float": ("setfloating", ""),
            "tile": ("settiled", ""),
            "pin": ("pin", ""),
            "center": ("centerwindow", ""),
        }
        if state not in dispatchers:
            return False
        if not self.focus_window(window_id):
            return False
        name, argument = dispatchers[state]
        result = self._run("dispatch", name, argument) if argument else self._run(
            "dispatch", name
        )
        return result is not None and result.returncode == 0

    @traced(log)
    def swap_windows(self, window_id: str, direction: str) -> bool:
        """Swap a tiled window with its neighbour, keeping both tiled."""
        if direction not in ("l", "r", "u", "d"):
            return False
        if not self.focus_window(window_id):
            return False
        result = self._run("dispatch", "swapwindow", direction)
        return result is not None and result.returncode == 0

    @traced(log)
    def group_windows(self, window_id: str, direction: str = "r") -> bool:
        """Fold a window into a tabbed group with the one beside it.

        `moveintoorcreategroup`, not `moveintogroup`: the latter only joins a
        neighbour that is *already* a group, and reports success when there is
        nothing to join, so two ordinary windows never grouped. Confirmed in
        the source and then measured.
        """
        if direction not in ("l", "r", "u", "d"):
            return False
        if not self.focus_window(window_id):
            return False
        result = self._run("dispatch", "moveintoorcreategroup", direction)
        return result is not None and result.returncode == 0

    @traced(log)
    def ungroup_window(self, window_id: str) -> bool:
        if not self.focus_window(window_id):
            return False
        result = self._run("dispatch", "moveoutofgroup")
        return result is not None and result.returncode == 0

    @traced(log)
    def float_window(self, address: str) -> bool:
        """Float one window, by address."""
        result = self._run("dispatch", "setfloating", f"address:{address}")
        return result is not None and result.returncode == 0

    @traced(log)
    def preselect(self, direction: str) -> bool:
        """Open the next window to one side of the current one.

        This is how a tiling compositor is told where a window goes: placement is
        decided when the window maps, not adjusted afterwards. Tiling also means
        gaps, borders and the space reserved by the bars are honoured for free —
        all of which had to be computed by hand when windows were floated.
        """
        if direction not in ("l", "r", "u", "d"):
            return False
        result = self._run("dispatch", "layoutmsg", "preselect", direction)
        return result is not None and result.returncode == 0

    @traced(log)
    def apply_ratios(self, handle: str, slots) -> int:
        """Resize tiled windows so they match the layout's proportions.

        `preselect` only decides which side a window opens on, so every split
        starts even. `resizewindowpixel` works on tiled windows too — it drives
        the split the way dragging the divider does, and the neighbour reflows to
        match — so the sizes can be corrected afterwards without floating
        anything.

        Sizes are a fraction of the area the windows actually occupy, measured
        from the windows themselves rather than the monitor, so gaps, borders and
        the space reserved by bars are all accounted for.
        """
        if len(slots) < 2:
            return 0

        clients = self._clients_on(handle)
        if len(clients) < 2:
            return 0

        area = self._tiled_area(clients)
        if area is None:
            return 0
        span_x, span_y = area

        resized = 0
        # The last window is left alone: it takes whatever its neighbours leave,
        # and resizing it would undo the split just set.
        for client, slot in list(zip(clients, slots))[:-1]:
            width = max(80, int(round(slot.width * span_x)))
            height = max(60, int(round(slot.height * span_y)))
            result = self._run(
                "dispatch",
                "resizewindowpixel",
                f"exact {width} {height},address:{client['address']}",
            )
            if result is not None and result.returncode == 0:
                resized += 1
        return resized

    def _clients_on(self, handle: str) -> list[dict]:
        data = self._query("clients")
        if not isinstance(data, list):
            return []
        return [
            c
            for c in data
            if isinstance(c, dict)
            and str((c.get("workspace") or {}).get("name", "")) == handle
            and c.get("address")
        ]

    def _tiled_area(self, clients: list[dict]) -> tuple[int, int] | None:
        """The rectangle the tiled windows span, as (width, height)."""
        try:
            lefts = [int(c["at"][0]) for c in clients]
            tops = [int(c["at"][1]) for c in clients]
            rights = [int(c["at"][0]) + int(c["size"][0]) for c in clients]
            bottoms = [int(c["at"][1]) + int(c["size"][1]) for c in clients]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return max(rights) - min(lefts), max(bottoms) - min(tops)

    @traced(log)
    def remove_workspace(self, handle: str) -> bool:
        # Named workspaces disappear on their own once the last window closes,
        # and the handle stays valid because it is a name, not a position.
        return not self.workspace_exists(handle)


def _in_layout_order(found: list[dict]) -> list[dict]:
    """Top-left to bottom-right, which is the order a layout's slots are in."""
    return sorted(found, key=lambda c: (c["y"], c["x"]))

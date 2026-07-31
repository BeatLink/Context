"""Keeping Context's workspaces under Context's control.

Context positions windows itself, so a window that opens on one of its
workspaces must float rather than tile. Hyprland has no rule that expresses
this: workspace rules have no float field, and a `match:workspace` window rule
is accepted but never applied — it is silently a no-op.

So ownership is enforced by watching the event socket. `openwindow` reports the
address and the workspace a window mapped on; if that workspace belongs to a
context, the window is floated immediately.

This is the piece that makes Context a window manager for its own workspaces
rather than a launcher that arranges windows once and then loses control of them.
"""

from __future__ import annotations

import os
import socket
import threading

from .backends.hyprland import HANDLE_PREFIX
from .logging_setup import get_logger

log = get_logger("watcher")


def _socket_path() -> str | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime or not signature:
        return None
    return f"{runtime}/hypr/{signature}/.socket2.sock"


def owns(workspace: str) -> bool:
    """Whether a workspace belongs to a context."""
    return workspace.startswith(HANDLE_PREFIX)


class WorkspaceWatcher:
    """Floats every window that opens on a context's workspace."""

    def __init__(self, backend) -> None:
        self.backend = backend
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        path = _socket_path()
        if path is None:
            log.warning("no Hyprland instance in the environment; watcher disabled")
            return False
        if not os.path.exists(path):
            log.warning("event socket missing at %s; watcher disabled", path)
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(path,), daemon=True)
        self._thread.start()
        log.info("watching %s for windows on context workspaces", path)
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self, path: str) -> None:
        try:
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.settimeout(1.0)
            conn.connect(path)
        except OSError as exc:
            log.error("could not connect to the event socket: %s", exc)
            return

        log.debug("event socket connected")
        buffer = b""
        with conn:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return

                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self._handle(line.decode("utf-8", "replace"))

    def _handle(self, line: str) -> None:
        # openwindow>>address,workspace,class,title
        if not line.startswith("openwindow>>"):
            return
        parts = line[len("openwindow>>") :].split(",", 3)
        if len(parts) < 2:
            return
        address, workspace = parts[0].strip(), parts[1].strip()
        if not address or not owns(workspace):
            return
        # The event reports the address without the 0x prefix that hyprctl's
        # address: selector requires, so dispatching it verbatim silently matches
        # nothing.
        if not address.startswith("0x"):
            address = f"0x{address}"
        ok = self.backend.float_window(address)
        log.debug("floating %s on %s -> %s", address, workspace, ok)
        if not ok:
            log.warning("could not float %s on %s", address, workspace)

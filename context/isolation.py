"""Launching an application so it cannot find a running copy of itself.

An application that cannot see its twin cannot hand off to it. No hand-off means
the process Context spawns is the process that owns the window — which is what
makes `--new-window` unnecessary, single-instance applications stop silently
doing nothing, and window identity tractable at all.

Applications find each other over three channels:

    D-Bus session bus     DBUS_SESSION_BUS_ADDRESS  — handled here
    unix sockets          XDG_RUNTIME_DIR           — cannot be isolated, see below
    lock files            the app's own data directory — per-adapter

Only the bus is isolated. Redirecting `XDG_RUNTIME_DIR` looks equally right, and
is wrong: the Wayland socket lives there, so an application given a private one
has no display and dies with "cannot open display". Measured, not assumed. The
bus is the channel that actually carries hand-off, so isolating it is enough for
every application that uses GApplication, D-Bus activation, or both.

Sharing the Wayland socket leaks nothing: Wayland gives a client no way to
enumerate other clients — the display server tells an application about its own
surfaces and nothing else.

Measured on a live session with Tilix, which is D-Bus activated and the reason
the single-instance switch exists: launching it twice on the session bus gives
one window, and twice under a private bus gives two windows with two distinct
pids.

**This is not a sandbox.** The application keeps full access to the filesystem
and the network. The point is invisibility between instances, not confinement,
and nothing here should be described as security.
"""

from __future__ import annotations

import shutil

from .logging_setup import get_logger

log = get_logger("isolation")

def available() -> bool:
    """Whether a private bus can be started at all."""
    return shutil.which("dbus-run-session") is not None


def isolate_env(env: dict[str, str], context_id: str) -> dict[str, str]:
    """`env` with the channel an application uses to find its twin removed.

    The bus address is *removed* rather than pointed somewhere:
    `dbus-run-session` starts a bus and sets the address itself, and an address
    already in the environment would be inherited instead.

    `XDG_RUNTIME_DIR` is deliberately left alone. Redirecting it looks right —
    it is where unix sockets live — but the Wayland socket lives there too, so
    a redirected one has no display to connect to. Measured: the application
    starts and dies with "cannot open display". The bus is the channel that
    matters for hand-off; the rest of the runtime directory is shared.
    """
    isolated = dict(env)
    isolated.pop("DBUS_SESSION_BUS_ADDRESS", None)
    isolated.pop("DBUS_STARTER_ADDRESS", None)
    isolated.pop("DBUS_STARTER_BUS_TYPE", None)
    return isolated


def wrap(command: list[str]) -> list[str]:
    """`command`, run under a private session bus.

    Returns it unchanged when `dbus-run-session` is missing, so isolation
    degrades to an ordinary launch rather than failing to launch at all.
    """
    if not command:
        return command
    binary = shutil.which("dbus-run-session")
    if binary is None:
        log.warning("dbus-run-session is not installed; launching unisolated")
        return command
    return [binary, "--", *command]



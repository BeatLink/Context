"""Backend selection.

Hyprland is the only window manager Context drives. Anything else falls back to
the null backend, where apps launch onto the current workspace.
"""

from __future__ import annotations

from context.system import backends
from context.system.backends import NullBackend


def test_only_hyprland_and_none_are_offered():
    assert set(backends.BACKENDS) == {"hyprland", "none"}


def test_cinnamon_is_gone():
    assert "cinnamon" not in backends.BACKENDS
    assert not hasattr(backends, "CinnamonBackend")


def test_a_retired_backend_name_is_ignored_not_obeyed(monkeypatch, caplog):
    """A leftover CONTEXT_BACKEND=cinnamon must not pin the session to nothing.

    The name is gone, so detection runs as if it had never been set — and says
    so, since silently ignoring configuration is how a session ends up on a
    backend the user did not expect.
    """
    monkeypatch.setenv("CONTEXT_BACKEND", "cinnamon")
    detected = backends.detect()
    assert detected.name in {"hyprland", "none"}
    assert "unknown backend" in caplog.text


def test_the_override_is_honoured(monkeypatch):
    monkeypatch.setenv("CONTEXT_BACKEND", "none")
    assert backends.detect().name == "none"


def test_the_null_backend_holds_nothing_open():
    assert NullBackend().live_handles() == set()

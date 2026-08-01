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


def test_a_context_called_home_does_not_land_on_the_overviews_workspace(monkeypatch):
    """The handle is derived from the title, and "Home" derives the overview's
    own. They would then be the same workspace: opening the context would put
    its apps on the overview, and closing it would close the overview."""
    from context.system.backends.hyprland import HOME_HANDLE, HyprlandBackend

    wm = HyprlandBackend()
    monkeypatch.setattr(wm, "workspace_names", lambda: [])

    assert wm.home_handle() == HOME_HANDLE
    assert wm.ensure_workspace("Home", None).handle != HOME_HANDLE
    # A context that already holds the name keeps it — dropping a stored handle
    # would orphan its workspace, which is worse than sharing one.
    assert wm.ensure_workspace("Home", HOME_HANDLE).handle == HOME_HANDLE


def test_the_null_backend_has_no_home():
    """Without workspaces there is nowhere for home to be, so the overview
    falls back to being an ordinary window."""
    from context.system.backends import NullBackend

    assert NullBackend().home_handle() is None


def test_the_overview_is_pinned_to_home_by_a_window_rule(monkeypatch):
    """Placement cannot be an ordering the caller arranges: present() returns
    long before the surface is committed, and the sidebar handing the keyboard
    back in between moved the active workspace out from under the map — which
    is how the overview ended up in whatever context you came from.

    The 0.56 syntax is `match:` with a space before the value. `class:foo` is
    answered "invalid field class:foo: missing a value", so a rule written the
    old way is accepted by nothing and silently places nothing.
    """
    from context.system.backends.hyprland import HOME_HANDLE, HyprlandBackend

    wm = HyprlandBackend()
    sent = []

    class Result:
        returncode = 0

    monkeypatch.setattr(wm, "_run", lambda *a: sent.append(a) or Result())

    assert wm.bind_to_home("io.beatlink.Context", "Overview") is True
    rule = sent[0][2]
    assert sent[0][:2] == ("keyword", "windowrule")
    assert rule.startswith(f"workspace name:{HOME_HANDLE} silent,")
    assert "match:class io.beatlink.Context" in rule
    assert "match:title Overview" in rule
    # `silent`, so installing the rule and rebuilding the window after a
    # restart does not drag you to home.
    assert "silent" in rule

    # Nothing to pin without both halves; a rule matching every window of the
    # class would send the launcher itself to home.
    assert wm.bind_to_home("io.beatlink.Context", "") is False
    assert wm.bind_to_home("", "Overview") is False


def test_the_overviews_titlebar_is_suppressed_by_the_plugins_own_field_name():
    """hyprbars registers the effect as `hyprbars:no_bar`, read out of its
    source. `nobar` is answered "missing a value" and `plugin:hyprbars:no_bar`
    "invalid field type" — and `bar_blacklist`, which does not exist at all,
    is answered "ok" and silently does nothing. Only the source settles it."""
    from context.system.backends.hyprland import HyprlandBackend

    wm = HyprlandBackend()
    sent = []

    class Result:
        returncode = 0

    wm._run = lambda *a: sent.append(a) or Result()

    assert wm.hide_titlebar("io.beatlink.Context", "Overview") is True
    rule = sent[0][2]
    assert sent[0][:2] == ("keyword", "windowrule")
    assert rule.startswith("hyprbars:no_bar on,")
    assert not rule.startswith("plugin:")
    assert "match:class io.beatlink.Context" in rule
    assert "match:title Overview" in rule

    # Both halves, or the rule would undecorate every window of the class —
    # the launcher included.
    assert wm.hide_titlebar("io.beatlink.Context", "") is False
    assert wm.hide_titlebar("", "Overview") is False


def test_the_null_backend_decorates_nothing_and_has_no_home():
    from context.system.backends import NullBackend

    wm = NullBackend()
    assert wm.hide_titlebar("a", "b") is False
    assert wm.bind_to_home("a", "b") is False

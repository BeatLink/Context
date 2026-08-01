"""Launching an app so it cannot find a running copy of itself.

Measured on a live session with Tilix, which is D-Bus activated: launched twice
on the session bus it gives one window, and twice under a private bus it gives
two windows with two distinct pids. These tests pin the wiring that produces
that, not the compositor behaviour itself.
"""

from __future__ import annotations

import pytest

from context.system import isolation
from context.system.adapters import base
from context.state.resources import Resource
from context.state.store import Context


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    yield tmp_path


def test_the_bus_address_is_removed_not_redirected():
    """dbus-run-session sets its own address; an inherited one would win."""
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/real/bus"}
    out = isolation.isolate_env(env, "ctx-1")
    assert "DBUS_SESSION_BUS_ADDRESS" not in out


def test_the_starter_bus_goes_too():
    """An activated app reads DBUS_STARTER_ADDRESS instead."""
    env = {
        "DBUS_STARTER_ADDRESS": "unix:path=/real/bus",
        "DBUS_STARTER_BUS_TYPE": "session",
    }
    out = isolation.isolate_env(env, "ctx-1")
    assert "DBUS_STARTER_ADDRESS" not in out
    assert "DBUS_STARTER_BUS_TYPE" not in out


def test_the_runtime_directory_is_left_alone():
    """Redirecting it looks right and breaks the display.

    The Wayland socket lives in XDG_RUNTIME_DIR, so an app given a private one
    has nothing to connect to. Measured: it starts and dies with "cannot open
    display". Only the bus carries hand-off, so only the bus is isolated.
    """
    env = {
        "WAYLAND_DISPLAY": "wayland-1",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS": "unix:/x",
    }
    out = isolation.isolate_env(env, "ctx-1")
    assert out["WAYLAND_DISPLAY"] == "wayland-1"
    assert out["XDG_RUNTIME_DIR"] == "/run/user/1000"


def test_wrap_puts_the_command_under_a_private_bus(monkeypatch):
    monkeypatch.setattr(isolation.shutil, "which", lambda _n: "/usr/bin/dbus-run-session")
    assert isolation.wrap(["tilix"]) == ["/usr/bin/dbus-run-session", "--", "tilix"]


def test_wrap_degrades_rather_than_failing(monkeypatch):
    """A missing dbus-run-session must not stop the app launching at all."""
    monkeypatch.setattr(isolation.shutil, "which", lambda _n: None)
    assert isolation.wrap(["tilix"]) == ["tilix"]


def test_nothing_is_isolated_by_default():
    assert base.isolated_context() is None
    assert base.child_command(["tilix"]) == ["tilix"]


def test_the_marker_only_applies_inside_the_block(monkeypatch):
    monkeypatch.setattr(isolation.shutil, "which", lambda _n: "/bin/dbus-run-session")
    with base.isolating("ctx-1"):
        assert base.isolated_context() == "ctx-1"
        assert base.child_command(["tilix"])[0] == "/bin/dbus-run-session"
    assert base.isolated_context() is None
    assert base.child_command(["tilix"]) == ["tilix"]


def test_the_marker_is_restored_after_a_failure(monkeypatch):
    """A launch that raises must not leave later launches isolated."""
    with pytest.raises(RuntimeError):
        with base.isolating("ctx-1"):
            raise RuntimeError("boom")
    assert base.isolated_context() is None


def test_child_env_isolates_only_inside_the_block(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/real")
    assert "DBUS_SESSION_BUS_ADDRESS" in base.child_env()
    with base.isolating("ctx-1"):
        assert "DBUS_SESSION_BUS_ADDRESS" not in base.child_env()


# -- the decision ------------------------------------------------------------


def _decide(ctx, resource, available=True, monkeypatch=None):
    from context.system import launcher

    monkeypatch.setattr(isolation, "available", lambda: available)
    return launcher._isolation_for(ctx, resource)


def test_a_plain_context_is_not_isolated(monkeypatch):
    ctx = Context(title="work")
    assert _decide(ctx, Resource(app_id="a"), monkeypatch=monkeypatch) is None


def test_an_isolated_context_isolates_its_apps(monkeypatch):
    ctx = Context(title="work", isolated=True)
    assert _decide(ctx, Resource(app_id="a"), monkeypatch=monkeypatch) == ctx.id


def test_an_app_can_opt_out_of_an_isolated_context(monkeypatch):
    """Both have to agree, because the app is what knows about its database."""
    ctx = Context(title="work", isolated=True)
    resource = Resource(app_id="notes", isolate=False)
    assert _decide(ctx, resource, monkeypatch=monkeypatch) is None


def test_isolation_is_skipped_when_unavailable(monkeypatch):
    ctx = Context(title="work", isolated=True)
    resource = Resource(app_id="a")
    assert _decide(ctx, resource, available=False, monkeypatch=monkeypatch) is None


# -- persistence -------------------------------------------------------------


def test_the_context_flag_round_trips(isolated_store):
    from context.state.store import ContextStore

    store = ContextStore()
    ctx = store.create("work")
    ctx.isolated = True
    store.save()

    assert ContextStore().contexts[0].isolated is True


def test_the_app_flag_survives_being_switched_off():
    """Falsy booleans have to be written, or the switch comes back on."""
    resource = Resource(app_id="notes", isolate=False)
    assert resource.to_dict()["isolate"] is False
    assert Resource.from_dict(resource.to_dict()).isolate is False


def test_apps_are_isolated_by_default_within_an_isolated_context():
    assert Resource(app_id="a").isolate is True

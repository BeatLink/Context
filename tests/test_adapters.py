"""Adapter routing, and the Firefox adapter's decisions.

The Firefox tests never start a browser: they assert on the command that would
be run, which is where the interesting logic lives.
"""

from __future__ import annotations

import pytest

from context.adapters import adapter_for, configurable, describe, supports_profiles
from context.adapters.base import GenericAdapter, child_env
from context.adapters.firefox import FirefoxAdapter, profiles_root
from context.resources import PROFILE_DEDICATED, PROFILE_MAIN, Resource


def test_firefox_resources_route_to_the_firefox_adapter():
    assert isinstance(adapter_for(Resource(app_id="firefox.desktop")), FirefoxAdapter)


def test_other_apps_fall_back_to_generic():
    assert isinstance(adapter_for(Resource(app_id="anything.desktop")), GenericAdapter)


def test_only_browsers_offer_a_profile_choice():
    assert supports_profiles(Resource(app_id="firefox.desktop"))
    assert not supports_profiles(Resource(app_id="anything.desktop"))


def test_only_adapted_apps_are_configurable():
    assert configurable(Resource(app_id="firefox.desktop"))
    assert not configurable(Resource(app_id="anything.desktop"))


@pytest.mark.parametrize(
    "urls,expected",
    [
        ([], "no URLs yet"),
        (["https://reddit.com"], "reddit.com"),
        (["https://reddit.com", "https://example.com"], "reddit.com +1 more"),
    ],
)
def test_describe_summarises_urls(urls, expected):
    assert describe(Resource(app_id="firefox.desktop", urls=urls)) == expected


def test_describe_marks_only_the_main_profile():
    """A dedicated profile is the default, so it is the other one worth naming."""
    main = Resource(
        app_id="firefox.desktop", urls=["https://x.com"], profile_mode=PROFILE_MAIN
    )
    own = Resource(
        app_id="firefox.desktop", urls=["https://x.com"], profile_mode=PROFILE_DEDICATED
    )
    assert "main profile" in describe(main)
    assert "profile" not in describe(own)


def test_new_resources_default_to_a_dedicated_profile():
    """The default is what guarantees a context its own window: the main
    profile hands extra URLs to whatever window was last focused."""
    assert Resource(app_id="firefox.desktop").profile_mode == PROFILE_DEDICATED


def test_child_env_strips_the_layer_shell_preload(monkeypatch):
    """Injecting gtk4-layer-shell into a launched app segfaults Firefox.

    The launcher runs with it preloaded so it can dock itself, and children
    inherit the environment unless it is scrubbed.
    """
    monkeypatch.setenv("LD_PRELOAD", "/nix/store/x-gtk4-layer-shell-1.3.0/lib/lib.so")
    monkeypatch.setenv("CONTEXT_LAYER_SHELL_PRELOADED", "1")
    env = child_env()
    assert "LD_PRELOAD" not in env
    assert "CONTEXT_LAYER_SHELL_PRELOADED" not in env


def test_child_env_keeps_unrelated_preloads(monkeypatch):
    monkeypatch.setenv(
        "LD_PRELOAD", "/usr/lib/libfoo.so:/nix/store/x-gtk4-layer-shell-1.3.0/lib/l.so"
    )
    assert child_env()["LD_PRELOAD"] == "/usr/lib/libfoo.so"


def test_new_profile_is_seeded_with_urls(tmp_path, monkeypatch):
    """First run opens the configured URLs; later runs let session restore win."""
    adapter = FirefoxAdapter()
    commands: list[list[str]] = []

    monkeypatch.setattr(adapter, "executable", lambda: "/bin/firefox")
    monkeypatch.setattr(adapter, "_await_unlocked", lambda p: None)

    class FakeProcess:
        def wait(self, timeout=None):
            raise __import__("subprocess").TimeoutExpired("firefox", timeout)

    monkeypatch.setattr(
        "subprocess.Popen", lambda cmd, **kw: commands.append(cmd) or FakeProcess()
    )

    resource = Resource(
        app_id="firefox.desktop",
        urls=["https://a.com"],
        profile_mode=PROFILE_DEDICATED,
    )
    adapter.launch(resource, "ctx-1")
    assert "https://a.com" in commands[0]

    # Second launch: the profile now exists, so no URLs are passed.
    commands.clear()
    adapter.launch(resource, "ctx-1")
    assert "https://a.com" not in commands[0]


def test_busy_profile_is_reported_not_swallowed(tmp_path, monkeypatch):
    """Firefox exits 1 and silently when its profile is still held.

    Popen without checking made that look like a successful launch, and the
    context came back empty.
    """
    adapter = FirefoxAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/firefox")
    monkeypatch.setattr(adapter, "_await_unlocked", lambda p: None)

    class ExitsImmediately:
        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: ExitsImmediately())

    with pytest.raises(LookupError, match="profile may still be in use"):
        adapter.launch(
            Resource(app_id="firefox.desktop", profile_mode=PROFILE_DEDICATED),
            "ctx-1",
        )


def test_teardown_never_leaves_the_profiles_root(isolated_store, monkeypatch):
    """A crafted profile name must not delete something elsewhere."""
    adapter = FirefoxAdapter()
    outside = isolated_store / "important"
    outside.mkdir(parents=True)

    resource = Resource(app_id="firefox.desktop", profile="../../important")
    adapter.teardown(resource, "ctx-1")
    assert outside.exists()


def test_teardown_leaves_the_main_profile_alone(isolated_store):
    adapter = FirefoxAdapter()
    target = profiles_root() / "ctx-1"
    target.mkdir(parents=True)

    adapter.teardown(
        Resource(app_id="firefox.desktop", profile_mode=PROFILE_MAIN), "ctx-1"
    )
    assert target.exists()


def test_teardown_removes_a_dedicated_profile(isolated_store):
    adapter = FirefoxAdapter()
    target = profiles_root() / "ctx-1"
    target.mkdir(parents=True)

    adapter.teardown(
        Resource(app_id="firefox.desktop", profile_mode=PROFILE_DEDICATED), "ctx-1"
    )
    assert not target.exists()


# -- VS Code -----------------------------------------------------------------


def test_vscode_resources_route_to_the_vscode_adapter():
    from context.adapters.vscode import VSCodeAdapter

    assert isinstance(adapter_for(Resource(app_id="codium.desktop")), VSCodeAdapter)
    assert isinstance(adapter_for(Resource(app_id="code.desktop")), VSCodeAdapter)


def test_vscode_offers_a_path_not_a_profile():
    from context.adapters import supports_paths

    resource = Resource(app_id="codium.desktop")
    assert supports_paths(resource)
    assert not supports_profiles(resource)


def test_target_kind_distinguishes_the_three_forms(tmp_path):
    """The CLI infers folder/file/workspace from the path; so must the UI."""
    from context.adapters.vscode import target_kind

    folder = tmp_path / "project"
    folder.mkdir()
    workspace = tmp_path / "thing.code-workspace"
    workspace.write_text("{}")
    plain = tmp_path / "notes.txt"
    plain.write_text("")

    assert target_kind(str(folder)) == "folder"
    assert target_kind(str(workspace)) == "workspace"
    assert target_kind(str(plain)) == "file"
    assert target_kind(None) == "none"


def test_vscode_describe_names_the_target(tmp_path):
    folder = tmp_path / "Context"
    folder.mkdir()
    workspace = tmp_path / "Context.code-workspace"
    workspace.write_text("{}")

    assert describe(Resource(app_id="codium.desktop", path=str(folder))) == "Context"
    assert (
        describe(Resource(app_id="codium.desktop", path=str(workspace)))
        == "Context (workspace)"
    )
    assert describe(Resource(app_id="codium.desktop")) == "no folder yet"


def test_vscode_passes_the_path_and_a_new_window(tmp_path, monkeypatch):
    from context.adapters.vscode import VSCodeAdapter

    adapter = VSCodeAdapter()
    commands: list[list[str]] = []
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/codium")
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: commands.append(cmd))

    folder = tmp_path / "project"
    folder.mkdir()
    adapter.launch(Resource(app_id="codium.desktop", path=str(folder)), "ctx-1")

    assert "--new-window" in commands[0]
    assert str(folder) in commands[0]


def test_vscode_rejects_a_missing_path(tmp_path, monkeypatch):
    """Opening a path that is gone gives an empty window, which looks like the
    context failing to restore rather than a bad path."""
    from context.adapters.vscode import VSCodeAdapter

    adapter = VSCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/codium")

    with pytest.raises(LookupError, match="does not exist"):
        adapter.launch(
            Resource(app_id="codium.desktop", path=str(tmp_path / "gone")), "ctx-1"
        )


# -- Terminals ---------------------------------------------------------------


def test_terminal_resources_route_to_the_terminal_adapter():
    from context.adapters.terminal import TerminalAdapter

    assert isinstance(
        adapter_for(Resource(app_id="com.gexperts.Tilix.desktop")), TerminalAdapter
    )


def test_terminal_offers_a_path_and_a_command():
    from context.adapters import supports_command, supports_paths

    resource = Resource(app_id="com.gexperts.Tilix.desktop")
    assert supports_paths(resource)
    assert supports_command(resource)
    assert not supports_profiles(resource)


def test_tilix_is_told_to_make_a_new_window(tmp_path, monkeypatch):
    """Tilix is D-Bus activated: plain `tilix` raises the existing window.

    Without --action=app-new-window a context including a terminal silently
    gets no terminal.
    """
    from context.adapters.terminal import TerminalAdapter

    adapter = TerminalAdapter()
    commands: list[list[str]] = []
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/tilix")
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: commands.append(cmd))

    adapter.launch(
        Resource(app_id="com.gexperts.Tilix.desktop", path=str(tmp_path)), "ctx-1"
    )
    assert "--action=app-new-window" in commands[0]
    assert str(tmp_path) in commands[0]


def test_single_instance_suppresses_the_new_window_flag(tmp_path, monkeypatch):
    """Asking a single-instance app for a new window either fails or is
    ignored, so the switch turns it off."""
    from context.adapters.terminal import TerminalAdapter

    adapter = TerminalAdapter()
    commands: list[list[str]] = []
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/tilix")
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: commands.append(cmd))

    adapter.launch(
        Resource(
            app_id="com.gexperts.Tilix.desktop",
            path=str(tmp_path),
            single_instance=True,
        ),
        "ctx-1",
    )
    assert "--action=app-new-window" not in commands[0]


def test_force_new_window_off_suppresses_it_too(monkeypatch):
    from context.adapters.terminal import TerminalAdapter

    adapter = TerminalAdapter()
    commands: list[list[str]] = []
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/tilix")
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: commands.append(cmd))

    adapter.launch(
        Resource(app_id="com.gexperts.Tilix.desktop", force_new_window=False), "ctx-1"
    )
    assert "--action=app-new-window" not in commands[0]


def test_terminal_command_comes_last(tmp_path, monkeypatch):
    """Most terminals treat everything after the command flag as the command."""
    from context.adapters.terminal import TerminalAdapter

    adapter = TerminalAdapter()
    commands: list[list[str]] = []
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/tilix")
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: commands.append(cmd))

    adapter.launch(
        Resource(
            app_id="com.gexperts.Tilix.desktop", path=str(tmp_path), command="htop"
        ),
        "ctx-1",
    )
    assert commands[0][-2:] == ["--command", "htop"]


def test_terminal_rejects_a_path_that_is_not_a_directory(tmp_path, monkeypatch):
    from context.adapters.terminal import TerminalAdapter

    adapter = TerminalAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/tilix")
    plain = tmp_path / "file.txt"
    plain.write_text("")

    with pytest.raises(LookupError, match="not a directory"):
        adapter.launch(
            Resource(app_id="com.gexperts.Tilix.desktop", path=str(plain)), "ctx-1"
        )


def test_compatibility_flags_survive_being_switched_off():
    """to_dict drops falsy values, which would lose a disabled switch."""
    resource = Resource(app_id="x.desktop", force_new_window=False)
    assert Resource.from_dict(resource.to_dict()).force_new_window is False


def test_main_profile_launch_never_waits_for_the_browser(monkeypatch):
    """Firefox must be watched, not waited on.

    `subprocess.run` was used here on the assumption that a Firefox was already
    running, so the invocation would hand its URL over and exit. When none was
    running the invocation became the browser instead and never returned — it
    held the launcher's main loop for as long as the browser lived, which read
    as Context freezing on launch.
    """
    adapter = FirefoxAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/firefox")

    ran: list[list[str]] = []

    def forbidden(*args, **kwargs):
        raise AssertionError("firefox must not be waited on to completion")

    monkeypatch.setattr("subprocess.run", forbidden)

    class NeverExits:
        def wait(self, timeout=None):
            raise __import__("subprocess").TimeoutExpired("firefox", timeout)

    monkeypatch.setattr(
        "subprocess.Popen", lambda cmd, **kw: ran.append(cmd) or NeverExits()
    )

    adapter.launch(
        Resource(
            app_id="firefox.desktop",
            urls=["https://a.com"],
            profile_mode=PROFILE_MAIN,
        ),
        "ctx-1",
    )
    assert ran == [["/bin/firefox", "--new-window", "https://a.com"]]


def test_main_profile_launch_is_detached(monkeypatch):
    """A browser that outlives the launcher must not be its child."""
    adapter = FirefoxAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/firefox")
    seen: dict = {}

    class NeverExits:
        def wait(self, timeout=None):
            raise __import__("subprocess").TimeoutExpired("firefox", timeout)

    def record(cmd, **kwargs):
        seen.update(kwargs)
        return NeverExits()

    monkeypatch.setattr("subprocess.Popen", record)
    adapter.launch(
        Resource(app_id="firefox.desktop", profile_mode=PROFILE_MAIN), "ctx-1"
    )
    assert seen["start_new_session"] is True


def test_main_profile_opens_a_window_then_tabs(monkeypatch):
    """The first URL makes the window; the rest join it."""
    adapter = FirefoxAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/firefox")
    ran: list[list[str]] = []

    class HandsOff:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(
        "subprocess.Popen", lambda cmd, **kw: ran.append(cmd) or HandsOff()
    )

    adapter.launch(
        Resource(
            app_id="firefox.desktop",
            urls=["https://a.com", "https://b.com", "https://c.com"],
            profile_mode=PROFILE_MAIN,
        ),
        "ctx-1",
    )
    assert [c[1] for c in ran] == ["--new-window", "--new-tab", "--new-tab"]


def test_main_profile_reports_a_failed_launch(monkeypatch):
    """A quick non-zero exit is still a failure, not a hand-off."""
    adapter = FirefoxAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: "/bin/firefox")

    class Crashes:
        def wait(self, timeout=None):
            return 2

    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: Crashes())

    with pytest.raises(LookupError, match="crashed on startup"):
        adapter.launch(
            Resource(app_id="firefox.desktop", profile_mode=PROFILE_MAIN), "ctx-1"
        )


def test_child_env_strips_electron_run_as_node(monkeypatch):
    """ELECTRON_RUN_AS_NODE makes every Electron app die on launch.

    It makes the binary run as plain Node, so it never builds a window and
    exits with "Cannot find module 'electron'". Editors set it for their own
    integrated terminals, so a Context started from one inherits it and every
    Electron application in every context fails — while the launch itself
    reports success, because the desktop entry was handed off fine.
    """
    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    monkeypatch.setenv("ELECTRON_NO_ATTACH_CONSOLE", "1")
    env = child_env()
    assert "ELECTRON_RUN_AS_NODE" not in env
    assert "ELECTRON_NO_ATTACH_CONSOLE" not in env


def test_child_env_keeps_the_rest_of_the_environment(monkeypatch):
    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    monkeypatch.setenv("ELECTRON_OZONE_PLATFORM_HINT", "auto")
    env = child_env()
    # Only the variables that break a launch go; the ones that configure one
    # correctly must survive.
    assert env["ELECTRON_OZONE_PLATFORM_HINT"] == "auto"


def test_the_launch_context_unsets_rather_than_only_setting(monkeypatch):
    """Gio copies the process environment, so removal has to be explicit."""
    from context.adapters import base

    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    unset: list[str] = []

    class FakeContext:
        def setenv(self, key, value):
            return None

        def unsetenv(self, key):
            unset.append(key)

    class FakeInfo:
        def launch(self, files, context):
            return True

    monkeypatch.setattr(base.Gio, "AppLaunchContext", lambda: FakeContext())
    monkeypatch.setattr(base.Gio.DesktopAppInfo, "new", lambda _id: FakeInfo())

    base.launch_desktop_entry("anything.desktop")
    assert "ELECTRON_RUN_AS_NODE" in unset

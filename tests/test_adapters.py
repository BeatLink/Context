"""Adapter routing, and the Firefox adapter's decisions.

The Firefox tests never start a browser: they assert on the command that would
be run, which is where the interesting logic lives.
"""

from __future__ import annotations

import pytest

from context.adapters import adapter_for, configurable, describe, supports_profiles
from context.adapters.base import GenericAdapter, child_env
from context.adapters.firefox import FirefoxAdapter, profiles_root
from context.resources import PROFILE_MAIN, Resource


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


def test_describe_marks_the_main_profile():
    resource = Resource(
        app_id="firefox.desktop", urls=["https://x.com"], profile_mode=PROFILE_MAIN
    )
    assert "main profile" in describe(resource)


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

    resource = Resource(app_id="firefox.desktop", urls=["https://a.com"])
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
        adapter.launch(Resource(app_id="firefox.desktop"), "ctx-1")


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

    adapter.teardown(Resource(app_id="firefox.desktop"), "ctx-1")
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

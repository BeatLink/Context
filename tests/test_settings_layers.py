"""The settings chain: several files, merged, last mention wins.

This is what lets a declaration and the settings screen coexist. The test that
matters most is `test_changing_one_setting_does_not_detach_the_rest`: writing a
full snapshot instead of the changed keys would pass every other test here and
still break the whole design.
"""

from __future__ import annotations

import json

import pytest

from context.state import settings
from context.state.settings import Settings


@pytest.fixture(autouse=True)
def isolated_layers(tmp_path, monkeypatch):
    """A config home and a system config dir of our own, and no env overrides."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "etc"))
    monkeypatch.delenv(settings.ENV_PATH, raising=False)
    monkeypatch.delenv(settings.ENV_LAYERS, raising=False)
    monkeypatch.setattr(settings, "_current", None)
    yield tmp_path


def _write(path, values) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))


def _system(tmp_path):
    return tmp_path / "etc" / "context" / "settings.json"


def _drop_in(tmp_path, name):
    return tmp_path / "config" / "context" / settings.DROP_IN_DIR / name


def _user(tmp_path):
    return tmp_path / "config" / "context" / "settings.json"


# -- order -------------------------------------------------------------------


def test_the_chain_runs_from_system_to_the_file_context_writes(isolated_layers):
    chain = settings.layers()
    assert chain[0] == _system(isolated_layers)
    assert chain[-1] == _user(isolated_layers)
    assert settings.settings_path() == chain[-1]


def test_the_system_directories_have_drop_ins_too(isolated_layers):
    """The NixOS module writes /etc/xdg/context/settings.d/NN-nixos.json, so a
    chain that only looked for settings.json there would never read it."""
    declared = (
        isolated_layers / "etc" / "context" / settings.DROP_IN_DIR / "20-nixos.json"
    )
    _write(declared, {"sidebar_width": 640})

    assert declared in settings.layers()
    assert Settings.load().sidebar_width == 640


def test_drop_ins_sit_between_the_system_and_the_writable_file(isolated_layers):
    _write(_drop_in(isolated_layers, "10-module.json"), {})
    chain = settings.layers()
    assert chain.index(_drop_in(isolated_layers, "10-module.json")) == 1
    assert chain[-1] == _user(isolated_layers)


def test_drop_ins_are_read_in_name_order(isolated_layers):
    _write(_drop_in(isolated_layers, "20-later.json"), {"sidebar_width": 700})
    _write(_drop_in(isolated_layers, "10-earlier.json"), {"sidebar_width": 500})
    assert Settings.load().sidebar_width == 700


def test_more_important_config_dirs_are_merged_later(isolated_layers, monkeypatch):
    """XDG_CONFIG_DIRS is most-important-first; merging takes the last mention,
    so the chain has to reverse it or the priority comes out backwards."""
    low = isolated_layers / "low"
    high = isolated_layers / "high"
    monkeypatch.setenv("XDG_CONFIG_DIRS", f"{high}:{low}")
    _write(high / "context" / "settings.json", {"sidebar_width": 700})
    _write(low / "context" / "settings.json", {"sidebar_width": 500})
    assert Settings.load().sidebar_width == 700


# -- merging -----------------------------------------------------------------


def test_the_last_file_to_mention_a_setting_decides_it(isolated_layers):
    _write(_system(isolated_layers), {"sidebar_width": 500})
    _write(_user(isolated_layers), {"sidebar_width": 900})
    assert Settings.load().sidebar_width == 900


def test_settings_no_later_file_mentions_keep_the_declared_value(isolated_layers):
    _write(_system(isolated_layers), {"sidebar_width": 500, "collapse_mode": "hidden"})
    _write(_user(isolated_layers), {"sidebar_width": 900})

    live = Settings.load()
    assert live.sidebar_width == 900
    assert live.collapse_mode == "hidden"


def test_a_broken_layer_does_not_take_the_rest_of_the_chain_down(isolated_layers):
    path = _drop_in(isolated_layers, "10-broken.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    _write(_system(isolated_layers), {"sidebar_width": 500})

    assert Settings.load().sidebar_width == 500


def test_a_layer_of_the_wrong_shape_is_ignored(isolated_layers):
    _write(_drop_in(isolated_layers, "10-list.json"), [1, 2, 3])
    _write(_system(isolated_layers), {"sidebar_width": 500})
    assert Settings.load().sidebar_width == 500


def test_a_missing_layer_is_simply_absent(isolated_layers):
    assert Settings.load().sidebar_width == Settings().sidebar_width


# -- what gets written -------------------------------------------------------


def test_only_the_changed_setting_is_written(isolated_layers):
    settings.update(sidebar_width=900)
    assert json.loads(_user(isolated_layers).read_text()) == {"sidebar_width": 900}


def test_changing_one_setting_does_not_detach_the_rest(isolated_layers):
    """The whole design in one test. Writing a snapshot of every setting would
    name every key in the writable layer, so every declared value below would be
    shadowed by a copy of its own default and the declaration would never apply
    again."""
    _write(_system(isolated_layers), {"collapse_mode": "hidden", "rail_width": 80})

    settings.update(sidebar_width=900)

    live = settings.current()
    assert live.sidebar_width == 900
    assert live.collapse_mode == "hidden"
    assert live.rail_width == 80
    assert set(json.loads(_user(isolated_layers).read_text())) == {"sidebar_width"}


def test_a_later_declaration_reaches_settings_that_were_never_changed(isolated_layers):
    settings.update(sidebar_width=900)
    # As if a rebuild rewrote the declared layer.
    _write(_system(isolated_layers), {"rail_width": 100, "sidebar_width": 400})

    live = settings.reload()
    assert live.rail_width == 100
    # Still the user's, because they said so and the declaration is below them.
    assert live.sidebar_width == 900


def test_changes_accumulate_rather_than_replacing_each_other(isolated_layers):
    settings.update(sidebar_width=900)
    settings.update(rail_width=80)
    stored = json.loads(_user(isolated_layers).read_text())
    assert stored == {"sidebar_width": 900, "rail_width": 80}


def test_saving_everything_is_still_available_and_writes_the_lot(isolated_layers):
    Settings().validated().save()
    stored = json.loads(_user(isolated_layers).read_text())
    assert "sidebar_width" in stored and "collapse_mode" in stored


# -- resetting ---------------------------------------------------------------


def test_resetting_gives_the_declared_value_back(isolated_layers):
    _write(_system(isolated_layers), {"sidebar_width": 500})
    settings.update(sidebar_width=900)
    assert settings.current().sidebar_width == 900

    settings.reset()
    assert settings.current().sidebar_width == 500
    assert json.loads(_user(isolated_layers).read_text()) == {}


def test_resetting_one_setting_leaves_the_others_changed(isolated_layers):
    settings.update(sidebar_width=900, rail_width=80)
    settings.reset("sidebar_width")

    assert settings.current().rail_width == 80
    assert settings.current().sidebar_width == Settings().sidebar_width


# -- being told the chain outright -------------------------------------------


def test_an_explicit_chain_replaces_the_inferred_one(isolated_layers, monkeypatch):
    first = isolated_layers / "a.json"
    second = isolated_layers / "b.json"
    _write(first, {"sidebar_width": 500, "rail_width": 80})
    _write(second, {"sidebar_width": 900})
    monkeypatch.setenv(settings.ENV_LAYERS, f"{first}:{second}")

    assert settings.layers() == [first, second]
    live = Settings.load()
    assert live.sidebar_width == 900
    assert live.rail_width == 80
    # The last of the named files is the one Context writes.
    assert settings.settings_path() == second


def test_a_single_file_override_still_replaces_everything(isolated_layers, monkeypatch):
    """`CONTEXT_SETTINGS` predates the chain and has to keep meaning what it
    did: this file and nothing else."""
    only = isolated_layers / "only.json"
    _write(only, {"sidebar_width": 500})
    _write(_system(isolated_layers), {"rail_width": 100})
    monkeypatch.setenv(settings.ENV_PATH, str(only))

    assert settings.layers() == [only]
    live = Settings.load()
    assert live.sidebar_width == 500
    assert live.rail_width == Settings().rail_width


# -- where a value came from -------------------------------------------------


def test_origins_name_the_file_that_decided_each_setting(isolated_layers):
    _write(_system(isolated_layers), {"sidebar_width": 500, "rail_width": 80})
    _write(_user(isolated_layers), {"sidebar_width": 900})

    where = settings.origins()
    assert where["sidebar_width"] == _user(isolated_layers)
    assert where["rail_width"] == _system(isolated_layers)
    assert "collapse_mode" not in where


def test_origins_ignore_keys_that_are_not_settings(isolated_layers):
    _write(_user(isolated_layers), {"nonsense": 1})
    assert "nonsense" not in settings.origins()


# -- the layout the Nix modules actually produce ------------------------------


def test_a_nixos_layer_a_home_layer_and_the_user_all_compose(isolated_layers):
    """The arrangement `nix/nixos-module.nix` and `nix/home-module.nix` create,
    end to end. Each declares only what it was given, which is why all three
    survive rather than the topmost one winning outright."""
    nixos = isolated_layers / "etc" / "context" / settings.DROP_IN_DIR / "20-nixos.json"
    home = _drop_in(isolated_layers, "50-home.json")

    _write(nixos, {"collapse_mode": "hidden", "sidebar_width": 420, "backend": "hyprland"})
    _write(home, {"sidebar_width": 500, "show_search": False})
    settings.update(sidebar_width=900)

    live = settings.reload()
    # The user beats home, home beats NixOS.
    assert live.sidebar_width == 900
    # Home said this and nothing above it did.
    assert live.show_search is False
    # NixOS said these and nothing above it did.
    assert live.collapse_mode == "hidden"
    assert live.backend == "hyprland"
    # And everything nobody mentioned is still Context's own default.
    assert live.rail_width == Settings().rail_width


def test_the_home_layer_does_not_bury_the_nixos_layer(isolated_layers):
    """The failure mode that made the modules write only what is set. With every
    option carrying a Nix default, the home file would name every key and undo a
    NixOS declaration purely by being enabled."""
    nixos = isolated_layers / "etc" / "context" / settings.DROP_IN_DIR / "20-nixos.json"
    _write(nixos, {"collapse_mode": "hidden"})
    _write(_drop_in(isolated_layers, "50-home.json"), {"sidebar_width": 500})

    assert Settings.load().collapse_mode == "hidden"


# -- declared contexts stack the same way ------------------------------------


def test_contexts_can_be_declared_in_more_than_one_file(isolated_layers, monkeypatch):
    from context.state import store

    monkeypatch.delenv(store.ENV_DECLARED, raising=False)
    base = isolated_layers / "config" / "context"
    _write(base / store.DECLARED_DIR / "10-work.json", {"contexts": [{"title": "Work"}]})
    _write(base / "contexts.json", {"contexts": [{"title": "Home"}]})

    titles = [c.title for c in store.declared_contexts()]
    assert titles == ["Work", "Home"]


def test_a_later_declaration_of_the_same_context_wins(isolated_layers, monkeypatch):
    from context.state import store

    monkeypatch.delenv(store.ENV_DECLARED, raising=False)
    base = isolated_layers / "config" / "context"
    _write(
        base / store.DECLARED_DIR / "10-a.json",
        {"contexts": [{"title": "Work", "apps": ["firefox.desktop"]}]},
    )
    _write(
        base / "contexts.json",
        {"contexts": [{"title": "Work", "apps": ["codium.desktop"]}]},
    )

    declared = store.declared_contexts()
    assert len(declared) == 1
    assert declared[0].apps == ["codium.desktop"]

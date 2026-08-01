"""Persistence, and staying readable across format changes."""

from __future__ import annotations

import json

from context.layout import Layout, Slot
from context.resources import PROFILE_DEDICATED, PROFILE_MAIN, Resource
from context.store import Context, ContextStore


def test_round_trip(isolated_store):
    store = ContextStore()
    ctx = store.create(
        "work",
        resources=[Resource(app_id="firefox.desktop", urls=["https://example.com"])],
    )
    ctx.layout = Layout(slots=[Slot(0.0, 0.0, 0.5, 1.0), Slot(0.5, 0.0, 0.5, 1.0)])
    ctx.set_handle("hyprland", "ctx-work")
    store.save()

    reloaded = ContextStore().contexts[0]
    assert reloaded.title == "work"
    assert reloaded.resources[0].urls == ["https://example.com"]
    assert len(reloaded.layout.slots) == 2
    assert reloaded.handle_for("hyprland") == "ctx-work"


def test_legacy_apps_list_still_loads():
    """Contexts written before resources existed used a flat list of app ids."""
    raw = {
        "title": "old",
        "id": "abc",
        "apps": ["firefox.desktop", "code.desktop"],
    }
    ctx = Context.from_dict(raw)
    assert [r.app_id for r in ctx.resources] == ["firefox.desktop", "code.desktop"]
    assert ctx.apps == ["firefox.desktop", "code.desktop"]


def test_unknown_fields_are_ignored():
    """A file written by a newer version must not crash an older one."""
    ctx = Context.from_dict({"title": "x", "someFutureField": 42})
    assert ctx.title == "x"


def test_profile_mode_defaults_to_the_main_profile():
    """Adding a browser opens the browser you already use.

    A dedicated profile arrives with no addons, logins or bookmarks, which is
    a surprise when all you did was add Firefox to a context. The window the
    context is owed comes from the launch order instead: the window opens
    first and the remaining URLs wait for it (see the firefox adapter).
    """
    assert Resource(app_id="firefox.desktop").uses_main_profile
    assert Resource.from_dict({"app_id": "firefox.desktop"}).uses_main_profile
    assert Resource.from_dict(
        {"app_id": "firefox.desktop", "profile_mode": "nonsense"}
    ).uses_main_profile


def test_a_stored_dedicated_profile_is_not_migrated():
    """Changing the default must not move existing contexts off their profiles.

    `to_dict` always writes profile_mode, so every saved resource carries its
    own choice; only a legacy file falls back to the default.
    """
    resource = Resource.from_dict(
        {"app_id": "firefox.desktop", "profile_mode": PROFILE_DEDICATED}
    )
    assert not resource.uses_main_profile
    assert resource.to_dict()["profile_mode"] == PROFILE_DEDICATED


def test_profile_mode_survives_a_round_trip():
    for mode in (PROFILE_MAIN, PROFILE_DEDICATED):
        original = Resource(app_id="firefox.desktop", profile_mode=mode)
        assert Resource.from_dict(original.to_dict()).profile_mode == mode


def test_search_is_case_insensitive(isolated_store):
    store = ContextStore()
    store.create("Surf Reddit")
    store.create("Work on Context")
    assert [c.title for c in store.search("REDDIT")] == ["Surf Reddit"]
    assert store.search("") and len(store.search("")) == 2
    assert store.search("nothing") == []


def test_touch_reorders_most_recent_first(isolated_store):
    store = ContextStore()
    first = store.create("first")
    store.create("second")
    store.touch(first)
    assert store.contexts[0].title == "first"


def test_delete_removes_from_disk(isolated_store):
    store = ContextStore()
    ctx = store.create("temporary")
    store.delete(ctx)
    assert ContextStore().contexts == []


def test_save_is_atomic(isolated_store):
    """A half-written file would lose every context, so writes go via a temp."""
    store = ContextStore()
    store.create("one")
    data = json.loads(store.path.read_text())
    assert data["version"] == 2
    assert not list(store.path.parent.glob("*.tmp"))


def test_declared_contexts_are_taken_in_once(tmp_path, monkeypatch):
    """Something else — a NixOS module, a dotfile — can declare contexts.

    They are seeds, not managed state: taken in once and ordinary from then on,
    so editing or forgetting one sticks.
    """
    import json

    from context.store import ContextStore, declared_id

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    declared = tmp_path / "declared.json"
    declared.write_text(
        json.dumps(
            {
                "contexts": [
                    {
                        "title": "Work on Context",
                        "resources": [{"app_id": "codium.desktop"}],
                        "isolated": True,
                    },
                    {"title": "Music", "apps": ["quodlibet.desktop"]},
                ]
            }
        )
    )
    monkeypatch.setenv("CONTEXT_DECLARED", str(declared))

    store = ContextStore()
    assert sorted(c.title for c in store.contexts) == ["Music", "Work on Context"]
    work = next(c for c in store.contexts if c.title == "Work on Context")
    assert work.id == declared_id("Work on Context")
    assert work.isolated is True
    assert work.apps == ["codium.desktop"]

    # Forgotten stays forgotten: a fresh store does not put it back.
    store.delete(work)
    assert [c.title for c in ContextStore().contexts] == ["Music"]

    # And a context edited after seeding is not overwritten by the declaration.
    music = next(c for c in ContextStore().contexts if c.title == "Music")
    music.title = "Listen"
    store.contexts = [music]
    store.save()
    assert [c.title for c in ContextStore().contexts] == ["Listen"]


def test_a_broken_declaration_is_ignored(tmp_path, monkeypatch):
    from context.store import ContextStore

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    broken = tmp_path / "declared.json"
    broken.write_text("{ not json")
    monkeypatch.setenv("CONTEXT_DECLARED", str(broken))

    assert ContextStore().contexts == []

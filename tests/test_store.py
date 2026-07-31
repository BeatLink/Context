"""Persistence, and staying readable across format changes."""

from __future__ import annotations

import json

from context.layout import Layout, Slot
from context.resources import PROFILE_MAIN, Resource
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


def test_profile_mode_defaults_and_validates():
    assert Resource.from_dict({"app_id": "firefox.desktop"}).profile_mode == "dedicated"
    assert Resource.from_dict(
        {"app_id": "firefox.desktop", "profile_mode": "nonsense"}
    ).profile_mode == "dedicated"
    resource = Resource.from_dict(
        {"app_id": "firefox.desktop", "profile_mode": PROFILE_MAIN}
    )
    assert resource.uses_main_profile


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

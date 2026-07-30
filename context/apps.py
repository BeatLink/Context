"""Discovery of installed applications."""

from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio


@dataclass(frozen=True)
class App:
    id: str
    name: str
    description: str
    icon: Gio.Icon | None

    @property
    def haystack(self) -> str:
        return f"{self.name}\n{self.description}".lower()


def installed_apps() -> list[App]:
    apps: dict[str, App] = {}
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        app_id = info.get_id()
        name = info.get_display_name()
        if not app_id or not name:
            continue
        apps[app_id] = App(
            id=app_id,
            name=name,
            description=info.get_description() or info.get_generic_name() or "",
            icon=info.get_icon(),
        )
    return sorted(apps.values(), key=lambda a: a.name.casefold())


def search_apps(apps: list[App], query: str) -> list[App]:
    q = query.strip().casefold()
    if not q:
        return list(apps)
    starts = [a for a in apps if a.name.casefold().startswith(q)]
    seen = {a.id for a in starts}
    contains = [a for a in apps if a.id not in seen and q in a.haystack]
    return starts + contains

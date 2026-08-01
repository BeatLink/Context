"""Discovery of installed applications."""

from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio


# The freedesktop registered main categories, in the order they are worth
# showing. Anything else an entry claims is a subcategory ("WebBrowser",
# "TextEditor") or a vendor's own, and grouping by those gives one heading per
# application.
MAIN_CATEGORIES: dict[str, str] = {
    "AudioVideo": "Media",
    "Development": "Development",
    "Education": "Education",
    "Game": "Games",
    "Graphics": "Graphics",
    "Network": "Internet",
    "Office": "Office",
    "Science": "Science",
    "Settings": "Settings",
    "System": "System",
    "Utility": "Utilities",
}


@dataclass(frozen=True)
class App:
    id: str
    name: str
    description: str
    icon: Gio.Icon | None
    # The main categories this entry claims, in the order above. Empty when it
    # claims none that are registered — "Other" as far as a filter is concerned.
    categories: tuple[str, ...] = ()

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
            categories=_categories(info),
        )
    return sorted(apps.values(), key=lambda a: a.name.casefold())


def _categories(info) -> tuple[str, ...]:
    """The registered main categories a desktop entry claims."""
    raw = ""
    getter = getattr(info, "get_categories", None)
    if getter is not None:
        raw = getter() or ""
    claimed = {part.strip() for part in raw.split(";") if part.strip()}
    return tuple(key for key in MAIN_CATEGORIES if key in claimed)


def categories_of(apps: list[App]) -> list[str]:
    """Which categories are worth offering, given what is installed."""
    present = {key for app in apps for key in app.categories}
    return [key for key in MAIN_CATEGORIES if key in present]


def in_category(apps: list[App], category: str) -> list[App]:
    """Apps in one category, or everything when it is empty."""
    if not category:
        return list(apps)
    return [app for app in apps if category in app.categories]


# How the grid can be ordered. Deliberately nothing that would need usage
# tracking Context does not do: "In contexts" is counted from the contexts
# that exist, which is the only record of what these applications are for.
SORTS: dict[str, str] = {
    "name": "A–Z",
    "contexts": "In contexts",
    "category": "By kind",
}


def sort_apps(apps: list[App], order: str, counts: dict[str, int] | None = None):
    """Order the grid. Unknown orders fall back to by name."""
    counts = counts or {}
    if order == "contexts":
        return sorted(
            apps, key=lambda a: (-counts.get(a.id, 0), a.name.casefold())
        )
    if order == "category":
        return sorted(
            apps,
            key=lambda a: (
                MAIN_CATEGORIES.get(a.categories[0], "~") if a.categories else "~",
                a.name.casefold(),
            ),
        )
    return sorted(apps, key=lambda a: a.name.casefold())


def search_apps(apps: list[App], query: str) -> list[App]:
    q = query.strip().casefold()
    if not q:
        return list(apps)
    starts = [a for a in apps if a.name.casefold().startswith(q)]
    seen = {a.id for a in starts}
    contains = [a for a in apps if a.id not in seen and q in a.haystack]
    return starts + contains

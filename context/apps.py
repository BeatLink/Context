"""Discovery of installed applications."""

from __future__ import annotations

import time
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
        # casefold to match what `search_apps` folds the query with; lower()
        # left "ß" unfindable by "ss".
        return f"{self.name}\n{self.description}".casefold()


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


# How the grid can be ordered, and what it is split into when it is. Each of
# these answers a different question — "the one I was just using", "the one
# whose name I know", "the ones I actually work in" — and each answers it with
# groups rather than one wall of icons.
SORTS: dict[str, str] = {
    "recent": "Recent",
    "name": "A–Z",
    "kind": "By kind",
    "contexts": "In contexts",
}

# Headings for the two halves of the "In contexts" split.
IN_CONTEXTS = "In contexts"
OUTSIDE_CONTEXTS = "Not in a context"

# And for the tail of the recency grouping: everything Context has never seen
# launched, which on a fresh install is everything.
NOT_YET_OPENED = "Not opened yet"

# For applications whose desktop entry claims no registered category.
UNCATEGORISED = "Everything else"

HOUR = 3600
DAY = 86400


def recency_heading(age: float) -> str:
    """How long ago, in the words a person would use.

    Coarse on purpose: the heading is what groups the grid, and per-minute
    headings would give one application each.
    """
    if age < HOUR:
        return "Just now"
    hours = int(age // HOUR)
    if hours < 24:
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    days = int(age // DAY)
    if days < 7:
        return "1 day ago" if days == 1 else f"{days} days ago"
    weeks = days // 7
    if weeks < 5:
        return "1 week ago" if weeks == 1 else f"{weeks} weeks ago"
    months = max(1, days // 30)
    return "1 month ago" if months == 1 else f"{months} months ago"


def arrange_apps(
    apps: list[App],
    order: str,
    times: dict[str, float] | None = None,
    counts: dict[str, int] | None = None,
    now: float | None = None,
) -> list[tuple[str, list[App]]]:
    """The grid's contents as (heading, apps) sections, in order."""
    if order == "recent":
        return by_recency(apps, times or {}, now=now)

    if order == "kind":
        return by_kind(apps)

    if order == "contexts":
        counts = counts or {}
        inside = [a for a in apps if counts.get(a.id)]
        outside = [a for a in apps if not counts.get(a.id)]
        inside.sort(key=lambda a: (-counts.get(a.id, 0), a.name.casefold()))
        outside.sort(key=lambda a: a.name.casefold())
        return [
            section
            for section in ((IN_CONTEXTS, inside), (OUTSIDE_CONTEXTS, outside))
            if section[1]
        ]

    return by_initial(apps)


def by_kind(apps: list[App]) -> list[tuple[str, list[App]]]:
    """Grouped under the categories the desktop entries claim.

    An application in two categories is filed under the first of them rather
    than listed twice: the grid is a place to look, and the same icon in two
    groups makes it a place to look twice.
    """
    groups: dict[str, list[App]] = {}
    for app in sorted(apps, key=lambda a: a.name.casefold()):
        key = app.categories[0] if app.categories else ""
        groups.setdefault(key, []).append(app)
    sections = [
        (MAIN_CATEGORIES[key], groups[key]) for key in MAIN_CATEGORIES if key in groups
    ]
    if "" in groups:
        sections.append((UNCATEGORISED, groups[""]))
    return sections


def by_recency(
    apps: list[App], times: dict[str, float], now: float | None = None
) -> list[tuple[str, list[App]]]:
    """Most recently launched first, in groups of how long ago that was."""
    moment = time.time() if now is None else now
    sections: list[tuple[str, list[App]]] = []
    unseen = [a for a in apps if a.id not in times]
    seen = sorted(
        (a for a in apps if a.id in times),
        key=lambda a: (-times[a.id], a.name.casefold()),
    )

    for app in seen:
        heading = recency_heading(max(0.0, moment - times[app.id]))
        if sections and sections[-1][0] == heading:
            sections[-1][1].append(app)
        else:
            sections.append((heading, [app]))

    if unseen:
        sections.append(
            (NOT_YET_OPENED, sorted(unseen, key=lambda a: a.name.casefold()))
        )
    return sections


def initial_of(app: App) -> str:
    """The heading an application is filed under: A–Z, or # for the rest."""
    first = app.name.strip()[:1].upper()
    return first if first.isalpha() else "#"


def by_initial(apps: list[App]) -> list[tuple[str, list[App]]]:
    """Alphabetical apps in lettered groups.

    A hundred icons in one run is a wall to read; the letters are what make it
    scannable, and what an application menu has always had.
    """
    groups: dict[str, list[App]] = {}
    for app in sorted(apps, key=lambda a: a.name.casefold()):
        groups.setdefault(initial_of(app), []).append(app)
    letters = sorted(k for k in groups if k != "#")
    return [(letter, groups[letter]) for letter in letters] + (
        [("#", groups["#"])] if "#" in groups else []
    )


def search_apps(apps: list[App], query: str) -> list[App]:
    q = query.strip().casefold()
    if not q:
        return list(apps)
    starts = [a for a in apps if a.name.casefold().startswith(q)]
    seen = {a.id for a in starts}
    contains = [a for a in apps if a.id not in seen and q in a.haystack]
    return starts + contains

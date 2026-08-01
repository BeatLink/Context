"""Persistence for context definitions."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .arrangement import Arrangement
from .logging_setup import get_logger
from .layout import Layout
from .resources import Resource, parse_resources


log = get_logger("store")


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "context"


@dataclass
class Context:
    title: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    resources: list[Resource] = field(default_factory=list)
    ephemeral: bool = False
    # Launch this context's apps under a private session bus, so they cannot
    # find a running copy of themselves and hand off to it. Off by default: two
    # copies of an application writing one database without knowing about each
    # other is how data gets lost, and not knowing is exactly what isolation
    # causes. Per-resource `isolate` opts an application out again.
    isolated: bool = False
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    # backend -> the context's primary handle. Screen 0, and what a
    # single-screen context has always had.
    workspaces: dict[str, str] = field(default_factory=dict)
    # backend -> screen index -> handle, for every screen beyond the first.
    # Kept apart from `workspaces` so a file written before contexts could span
    # still loads, and so the primary handle keeps its meaning.
    extra_workspaces: dict[str, dict[int, str]] = field(default_factory=dict)
    layout: Layout = field(default_factory=Layout)
    # Screen count -> how the context arranges itself with that many attached.
    # Docked and undocked are different arrangements, both remembered.
    arrangements: dict[int, Arrangement] = field(default_factory=dict)

    @property
    def apps(self) -> list[str]:
        return [r.app_id for r in self.resources]

    def resource_for(self, app_id: str) -> Resource | None:
        for resource in self.resources:
            if resource.app_id == app_id:
                return resource
        return None

    def handle_for(self, backend: str, screen: int = 0) -> str | None:
        if screen == 0:
            return self.workspaces.get(backend)
        return self.extra_workspaces.get(backend, {}).get(screen)

    def set_handle(self, backend: str, handle: str, screen: int = 0) -> None:
        if screen == 0:
            self.workspaces[backend] = handle
        else:
            self.extra_workspaces.setdefault(backend, {})[screen] = handle

    def handles_for(self, backend: str) -> list[str]:
        """Every handle this context owns, primary first.

        Closing, reconnecting and "is it open" all work over the whole set: a
        context spanning two screens is open when *any* of its workspaces has
        windows, and closing it has to shut all of them.
        """
        found = []
        primary = self.workspaces.get(backend)
        if primary:
            found.append(primary)
        for _, handle in sorted(self.extra_workspaces.get(backend, {}).items()):
            if handle:
                found.append(handle)
        return found

    def drop_handles(self, backend: str) -> None:
        self.workspaces.pop(backend, None)
        self.extra_workspaces.pop(backend, None)

    def arrangement_for(self, screens: int) -> Arrangement:
        """How this context arranges itself with `screens` attached.

        Falls back to the nearest smaller arrangement rather than an empty one,
        so plugging in a third monitor starts from the two-monitor layout
        instead of losing the work.
        """
        screens = max(1, screens)
        if screens in self.arrangements:
            return self.arrangements[screens]
        for count in sorted(self.arrangements, reverse=True):
            if count < screens:
                return self.arrangements[count]
        # Nothing stored: the legacy single layout is the one-screen answer.
        return Arrangement.from_layout(self.layout, len(self.resources))

    def set_arrangement(self, screens: int, arrangement: Arrangement) -> None:
        self.arrangements[max(1, screens)] = arrangement
        # The flat layout stays the single-screen truth, so an older Context
        # reading this file still finds something sensible.
        if screens == 1 and arrangement.screens:
            self.layout = arrangement.screens[0]

    @classmethod
    def from_dict(cls, raw: dict) -> "Context":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in raw.items() if k in known}
        # `apps` is the pre-resource form: a plain list of desktop-entry ids.
        data["resources"] = parse_resources(raw.get("resources") or raw.get("apps"))
        data["layout"] = Layout.from_list(raw.get("layout"))

        # JSON has no integer keys, so both of these come back stringified.
        extra: dict[str, dict[int, str]] = {}
        for backend, handles in (raw.get("extra_workspaces") or {}).items():
            if not isinstance(handles, dict):
                continue
            for screen, handle in handles.items():
                try:
                    extra.setdefault(str(backend), {})[int(screen)] = str(handle)
                except (TypeError, ValueError):
                    continue
        data["extra_workspaces"] = extra

        arrangements: dict[int, Arrangement] = {}
        for screens, entry in (raw.get("arrangements") or {}).items():
            try:
                arrangements[int(screens)] = Arrangement.from_dict(entry)
            except (TypeError, ValueError):
                continue
        data["arrangements"] = arrangements
        return cls(**data)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["resources"] = [r.to_dict() for r in self.resources]
        data["layout"] = self.layout.to_list()
        data["extra_workspaces"] = {
            backend: {str(screen): handle for screen, handle in sorted(handles.items())}
            for backend, handles in self.extra_workspaces.items()
        }
        data["arrangements"] = {
            str(screens): arrangement.to_dict()
            for screens, arrangement in sorted(self.arrangements.items())
        }
        return data


# Contexts declared outside Context — by a NixOS module, a dotfile, anything
# that writes the config directory. They are seeds, not managed state: each is
# copied into the store once and is an ordinary context from then on, so
# editing or forgetting one sticks rather than being undone at the next start.
DECLARED_NAME = "contexts.json"
SEEDED_KEY = "seeded_contexts"


def declared_path() -> Path:
    from .settings import config_dir

    override = os.environ.get(ENV_DECLARED)
    return Path(override) if override else config_dir() / DECLARED_NAME


ENV_DECLARED = "CONTEXT_DECLARED"


def declared_contexts() -> list[Context]:
    """Contexts written into the config directory by something else.

    The same shape as the store's own file, minus the bookkeeping: a title, the
    applications, and whatever options were meant. An id is derived from the
    title when none is given, so the same declaration is the same context
    across machines and across rewrites of the file.
    """
    path = declared_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring %s: %s", path, exc)
        return []

    entries = raw.get("contexts", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        log.warning("ignoring %s: expected a list of contexts", path)
        return []

    found = []
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get("title", "")).strip():
            continue
        entry = dict(entry)
        entry.setdefault("id", declared_id(str(entry["title"])))
        found.append(Context.from_dict(entry))
    return found


def declared_id(title: str) -> str:
    slug = "".join(
        c if (c.isalnum() or c in "-_") else "-" for c in title.strip().casefold()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"declared:{slug or 'untitled'}"


class ContextStore:
    def __init__(self, path: Path | None = None, seed: bool = True) -> None:
        self.path = path or (data_dir() / "contexts.json")
        self.contexts: list[Context] = []
        self.load()
        if seed:
            self.seed_declared()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.contexts = []
            return
        entries = raw.get("contexts", []) if isinstance(raw, dict) else []
        self.contexts = [Context.from_dict(e) for e in entries if isinstance(e, dict)]
        self._sort()

    def seed_declared(self) -> list[Context]:
        """Take in any declared context this store has not seen before.

        Once, and only once: a context that was seeded and then forgotten stays
        forgotten, which it could not if the declaration were re-applied every
        start. What has been taken in is recorded in `uistate` rather than in
        the store, since it is a fact about this machine and not about any
        context.
        """
        from . import uistate

        declared = declared_contexts()
        if not declared:
            return []

        seen = {i for i in uistate.get(SEEDED_KEY, []) if isinstance(i, str)}
        known = {c.id for c in self.contexts}
        added = []
        for ctx in declared:
            if ctx.id in known or ctx.id in seen:
                continue
            self.contexts.append(ctx)
            added.append(ctx)
        if added:
            uistate.save(**{SEEDED_KEY: sorted(seen | {c.id for c in added})})
            self._sort()
            self.save()
            log.info("took in %d declared context(s)", len(added))
        return added

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 2, "contexts": [c.to_dict() for c in self.contexts]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)

    def _sort(self) -> None:
        self.contexts.sort(key=lambda c: c.last_used_at, reverse=True)

    def create(
        self,
        title: str,
        resources: list[Resource] | None = None,
        ephemeral: bool = False,
    ) -> Context:
        ctx = Context(title=title.strip(), resources=resources or [], ephemeral=ephemeral)
        self.contexts.append(ctx)
        self._sort()
        self.save()
        return ctx

    def touch(self, ctx: Context) -> None:
        ctx.last_used_at = time.time()
        self._sort()
        self.save()

    def delete(self, ctx: Context) -> None:
        self.contexts = [c for c in self.contexts if c.id != ctx.id]
        self.save()

    def search(self, query: str) -> list[Context]:
        q = query.strip().lower()
        if not q:
            return list(self.contexts)
        return [c for c in self.contexts if q in c.title.lower()]

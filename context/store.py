"""Persistence for context definitions."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .resources import Resource, parse_resources


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "context"


@dataclass
class Context:
    title: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    resources: list[Resource] = field(default_factory=list)
    ephemeral: bool = False
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    workspaces: dict[str, str] = field(default_factory=dict)

    @property
    def apps(self) -> list[str]:
        return [r.app_id for r in self.resources]

    def resource_for(self, app_id: str) -> Resource | None:
        for resource in self.resources:
            if resource.app_id == app_id:
                return resource
        return None

    def handle_for(self, backend: str) -> str | None:
        return self.workspaces.get(backend)

    def set_handle(self, backend: str, handle: str) -> None:
        self.workspaces[backend] = handle

    @classmethod
    def from_dict(cls, raw: dict) -> "Context":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in raw.items() if k in known}
        # `apps` is the pre-resource form: a plain list of desktop-entry ids.
        data["resources"] = parse_resources(raw.get("resources") or raw.get("apps"))
        return cls(**data)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["resources"] = [r.to_dict() for r in self.resources]
        return data


class ContextStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "contexts.json")
        self.contexts: list[Context] = []
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.contexts = []
            return
        entries = raw.get("contexts", []) if isinstance(raw, dict) else []
        self.contexts = [Context.from_dict(e) for e in entries if isinstance(e, dict)]
        self._sort()

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

"""Notes, and every version they have ever had.

A note is not a body of text with an undo stack — it is an append-only list of
versions, and the body you see is the last one. Nothing is ever overwritten and
nothing is ever dropped, so a note cannot be lost to a bad edit.

The consequence worth understanding is what happens when you edit an old
version. Writing from version 2 while version 5 exists does *not* truncate 3, 4
and 5 the way a text editor's redo stack would: it appends version 6, recording
that it was written from 2. The history is a tree stored as a flat list, and the
list only ever grows.

    v1 ─ v2 ─ v3 ─ v4 ─ v5
          └── v6              edited from v2; v3..v5 are still there

That is what `base` on a version is for. `number` is the order the versions were
written, which is also the order they are stored; `base` is what each was
written *from*. The tip — the highest number — is always the current body,
because appending is the only way to change anything.

Notes belong to a context or to none. `context_id` is the owner and `GLOBAL`
(the empty string) means the note stands outside any context, so one store holds
both kinds and a note can be moved between them without migrating anything.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from context.state.store import data_dir
from context.system.logging_setup import get_logger

log = get_logger("scratchpad")

ENV_PATH = "CONTEXT_SCRATCHPAD"

# The context_id of a note that belongs to no context.
GLOBAL = ""

# What a line of a note is. Everything is TEXT unless it opens with a marker.
TEXT = "text"
BULLET = "bullet"
CHECKED = "checked"
UNCHECKED = "unchecked"
KINDS = (TEXT, BULLET, CHECKED, UNCHECKED)
BOXES = (CHECKED, UNCHECKED)

# `- item`, `* item`, `- [ ] item`, `- [x] item`. The marker needs whitespace
# after it so a line of prose beginning with a dash stays prose.
_MARKER = re.compile(
    r"^(?P<indent>[ \t]*)[-*][ \t]+(?:\[(?P<box>[ xX])\](?:[ \t]+|$))?(?P<text>.*)$"
)

# How many spaces make one step of nesting. A tab is always one step.
INDENT_WIDTH = 2


@dataclass(frozen=True)
class Line:
    kind: str
    text: str
    indent: int = 0

    @property
    def checked(self) -> bool:
        return self.kind == CHECKED

    @property
    def is_box(self) -> bool:
        return self.kind in BOXES

    def render(self) -> str:
        pad = " " * (self.indent * INDENT_WIDTH)
        if self.kind == TEXT:
            return f"{pad}{self.text}" if self.text else ""
        if self.kind == BULLET:
            return f"{pad}- {self.text}"
        return f"{pad}- [{'x' if self.kind == CHECKED else ' '}] {self.text}"

    def toggled(self) -> "Line":
        if not self.is_box:
            return self
        return Line(
            kind=UNCHECKED if self.kind == CHECKED else CHECKED,
            text=self.text,
            indent=self.indent,
        )


def parse(body: str) -> list[Line]:
    """A note's text as typed lines, one per line of the body."""
    lines = []
    for raw in (body or "").splitlines():
        match = _MARKER.match(raw)
        if match is None:
            stripped = raw.rstrip()
            indent = _indent_of(stripped)
            lines.append(Line(kind=TEXT, text=stripped.strip(), indent=indent))
            continue
        box = match.group("box")
        kind = (
            BULLET
            if box is None
            else (UNCHECKED if box.strip() == "" else CHECKED)
        )
        lines.append(
            Line(
                kind=kind,
                text=match.group("text").strip(),
                indent=_indent_of(match.group("indent")),
            )
        )
    return lines


def _indent_of(prefix: str) -> int:
    steps = 0
    for char in prefix:
        if char == "\t":
            steps += 1
        elif char == " ":
            steps += 1 / INDENT_WIDTH
        else:
            break
    return int(steps)


def render(lines: list[Line]) -> str:
    return "\n".join(line.render() for line in lines)


def toggle(body: str, index: int) -> str:
    """Flip the checkbox on one line, giving back the whole body.

    A line that is not a checkbox, or an index off the end, gives the body back
    unchanged — the caller is a click on a widget and should not have to check
    what it is clicking.
    """
    lines = parse(body)
    if not 0 <= index < len(lines) or not lines[index].is_box:
        return body
    lines[index] = lines[index].toggled()
    return render(lines)


def set_kind(body: str, index: int, kind: str) -> str:
    """Make one line a bullet, a checkbox or plain text, keeping its words."""
    lines = parse(body)
    if not 0 <= index < len(lines) or kind not in KINDS:
        return body
    lines[index] = Line(kind=kind, text=lines[index].text, indent=lines[index].indent)
    return render(lines)


def continuation(line: str) -> str:
    """The marker a new line should inherit when Enter is pressed on `line`.

    Typing a list is the common case and re-typing the marker every line is the
    tax on it. An empty item gives back nothing, so a second Enter ends the list
    rather than continuing it forever.
    """
    parsed = parse(line)
    if not parsed:
        return ""
    item = parsed[0]
    if item.kind == TEXT or not item.text:
        return ""
    pad = " " * (item.indent * INDENT_WIDTH)
    if item.kind == BULLET:
        return f"{pad}- "
    return f"{pad}- [ ] "


def progress(body: str) -> tuple[int, int]:
    """How many checkboxes are ticked, and how many there are."""
    boxes = [line for line in parse(body) if line.is_box]
    return sum(1 for line in boxes if line.checked), len(boxes)


def summary(body: str, limit: int = 80) -> str:
    """The first line with something on it, for a row that has one line to give."""
    for line in parse(body):
        if line.text:
            return line.text if len(line.text) <= limit else line.text[: limit - 1] + "…"
    return ""


@dataclass(frozen=True)
class Version:
    number: int
    body: str
    created_at: float = field(default_factory=time.time)
    # The version this one was written from. 0 for the first, and for anything
    # whose parent cannot be resolved.
    base: int = 0

    @classmethod
    def from_dict(cls, raw: dict) -> "Version | None":
        try:
            number = int(raw["number"])
        except (KeyError, TypeError, ValueError):
            return None
        if number < 1:
            return None
        try:
            created_at = float(raw.get("created_at", 0.0))
        except (TypeError, ValueError):
            created_at = 0.0
        try:
            base = int(raw.get("base", 0))
        except (TypeError, ValueError):
            base = 0
        return cls(
            number=number,
            body=str(raw.get("body", "")),
            created_at=created_at,
            base=max(0, base),
        )

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "body": self.body,
            "created_at": self.created_at,
            "base": self.base,
        }


@dataclass
class Note:
    title: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    context_id: str = GLOBAL
    versions: list[Version] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def body(self) -> str:
        return self.versions[-1].body if self.versions else ""

    @property
    def current(self) -> Version | None:
        return self.versions[-1] if self.versions else None

    @property
    def updated_at(self) -> float:
        tip = self.current
        return tip.created_at if tip else self.created_at

    @property
    def is_global(self) -> bool:
        return self.context_id == GLOBAL

    def version(self, number: int) -> Version | None:
        for entry in self.versions:
            if entry.number == number:
                return entry
        return None

    def revise(self, body: str, base: int | None = None) -> Version:
        """Append `body` as the newest version and return it.

        `base` is the version it was written from, defaulting to the tip — the
        ordinary case of editing what is on screen. Passing an older number is
        editing from history, which appends just the same: nothing between that
        version and the tip is touched.

        Writing exactly what the tip already says appends nothing and gives the
        tip back. Otherwise every focus-out would add a version identical to the
        one before it, and the history would be mostly noise.
        """
        tip = self.current
        if tip is not None and body == tip.body:
            return tip
        if base is None:
            base = tip.number if tip else 0
        elif self.version(base) is None:
            base = 0
        version = Version(
            number=(tip.number + 1) if tip else 1,
            body=body,
            created_at=time.time(),
            base=base,
        )
        self.versions.append(version)
        return version

    def restore(self, number: int) -> Version | None:
        """Bring an old version back as the newest one.

        The same append the editor does, so restoring is not a special kind of
        change: it is writing what that version said, recorded as having come
        from it.
        """
        wanted = self.version(number)
        if wanted is None:
            return None
        return self.revise(wanted.body, base=number)

    def children_of(self, number: int) -> list[Version]:
        return [v for v in self.versions if v.base == number]

    @classmethod
    def from_dict(cls, raw: dict) -> "Note | None":
        if not isinstance(raw, dict):
            return None
        versions = []
        for entry in raw.get("versions") or []:
            if isinstance(entry, dict):
                parsed = Version.from_dict(entry)
                if parsed is not None:
                    versions.append(parsed)
        versions.sort(key=lambda v: v.number)
        try:
            created_at = float(raw.get("created_at", 0.0))
        except (TypeError, ValueError):
            created_at = 0.0
        return cls(
            title=str(raw.get("title", "")),
            id=str(raw.get("id") or uuid.uuid4()),
            context_id=str(raw.get("context_id", GLOBAL)),
            versions=versions,
            created_at=created_at or time.time(),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "context_id": self.context_id,
            "created_at": self.created_at,
            "versions": [v.to_dict() for v in self.versions],
        }


def scratchpad_path() -> Path:
    override = os.environ.get(ENV_PATH)
    return Path(override) if override else data_dir() / "scratchpad.json"


class NoteStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or scratchpad_path()
        self.notes: list[Note] = []
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.notes = []
            return
        except (OSError, json.JSONDecodeError) as exc:
            # Unreadable notes must not stop the launcher starting, the same
            # rule the settings and the ui state follow.
            log.warning("ignoring %s: %s", self.path, exc)
            self.notes = []
            return
        entries = raw.get("notes", []) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            log.warning("ignoring %s: expected a list of notes", self.path)
            self.notes = []
            return
        found = []
        for entry in entries:
            note = Note.from_dict(entry)
            if note is not None:
                found.append(note)
        self.notes = found
        self._sort()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "notes": [n.to_dict() for n in self.notes]}
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("could not write %s: %s", self.path, exc)

    def _sort(self) -> None:
        self.notes.sort(key=lambda n: n.updated_at, reverse=True)

    def get(self, note_id: str) -> Note | None:
        for note in self.notes:
            if note.id == note_id:
                return note
        return None

    def create(self, title: str = "", context_id: str = GLOBAL, body: str = "") -> Note:
        note = Note(title=title.strip(), context_id=context_id)
        if body:
            note.revise(body)
        self.notes.append(note)
        self._sort()
        self.save()
        return note

    def revise(self, note: Note, body: str, base: int | None = None) -> Version:
        version = note.revise(body, base=base)
        self._sort()
        self.save()
        return version

    def rename(self, note: Note, title: str) -> None:
        note.title = title.strip()
        self.save()

    def move(self, note: Note, context_id: str) -> None:
        note.context_id = context_id
        self.save()

    def delete(self, note: Note) -> None:
        self.notes = [n for n in self.notes if n.id != note.id]
        self.save()

    def notes_for(self, context_id: str) -> list[Note]:
        """Every note owned by one context, or the global ones for `GLOBAL`."""
        return [n for n in self.notes if n.context_id == context_id]

    def globals(self) -> list[Note]:
        return self.notes_for(GLOBAL)

    def visible(self, context_id: str | None = None) -> list[Note]:
        """What the launcher should list, honouring the two settings.

        Whether global notes and a context's own notes are shown are separate
        choices, so this is the one place that reads both — a view asks for what
        to draw rather than working it out.
        """
        from context.state import settings

        live = settings.current()
        if not live.scratchpad:
            return []
        found = []
        if live.scratchpad_global:
            found.extend(self.globals())
        if live.scratchpad_per_context and context_id:
            found.extend(self.notes_for(context_id))
        found.sort(key=lambda n: n.updated_at, reverse=True)
        return found

    def search(self, query: str, context_id: str | None = None) -> list[Note]:
        q = query.strip().casefold()
        found = self.visible(context_id)
        if not q:
            return found
        return [
            n for n in found if q in n.title.casefold() or q in n.body.casefold()
        ]

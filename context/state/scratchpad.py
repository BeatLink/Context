"""One note for the desk, and one for each context.

Not a notes application. There is exactly one global scratchpad and exactly one
per context, they have no names, and there is nothing to create or delete — a
scratchpad you have to file something in is slower than the paper it replaces.
Which note you are typing into is decided by where you are, so the answer to
"where did I put that" is always "in the context I was in".

The body is plain text with line markers, saved as you type:

    - milk          a bullet
    - [ ] milk      an unticked checkbox
    - [x] milk      a ticked one
      - milk        two spaces per level of nesting

Keeping the text as the stored form rather than a document model is what makes
the file readable, the checklist a rendering rather than a second copy, and this
module small.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from context.state.store import data_dir
from context.system.logging_setup import get_logger

log = get_logger("scratchpad")

ENV_PATH = "CONTEXT_SCRATCHPAD"

# The key of the note that belongs to no context.
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
            lines.append(
                Line(kind=TEXT, text=stripped.strip(), indent=_indent_of(stripped))
            )
            continue
        box = match.group("box")
        kind = (
            BULLET if box is None else (UNCHECKED if box.strip() == "" else CHECKED)
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
    """The first line with something on it, for anywhere with one line to give."""
    for line in parse(body):
        if line.text:
            return line.text if len(line.text) <= limit else line.text[: limit - 1] + "…"
    return ""


@dataclass
class Note:
    context_id: str = GLOBAL
    body: str = ""
    updated_at: float = field(default_factory=time.time)

    @property
    def is_global(self) -> bool:
        return self.context_id == GLOBAL

    @property
    def is_empty(self) -> bool:
        return not self.body.strip()


def scratchpad_path() -> Path:
    override = os.environ.get(ENV_PATH)
    return Path(override) if override else data_dir() / "scratchpad.json"


class NoteStore:
    """Every scratchpad, keyed by the context it belongs to."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or scratchpad_path()
        self.notes: dict[str, Note] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.notes = {}
            return
        except (OSError, json.JSONDecodeError) as exc:
            # Unreadable notes must not stop the launcher starting, the same
            # rule the settings and the ui state follow.
            log.warning("ignoring %s: %s", self.path, exc)
            self.notes = {}
            return

        entries = raw.get("notes") if isinstance(raw, dict) else None
        if isinstance(entries, list):
            # The first shape this took: a list of titled notes, each with a
            # version history. Not read — a hard cutover, so anything written
            # under it is left behind rather than guessed at.
            log.warning("ignoring %s: written by the versioned scratchpad", self.path)
            self.notes = {}
            return
        if not isinstance(entries, dict):
            log.warning("ignoring %s: expected notes to be an object", self.path)
            self.notes = {}
            return

        found: dict[str, Note] = {}
        for context_id, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            try:
                updated_at = float(entry.get("updated_at", 0.0))
            except (TypeError, ValueError):
                updated_at = 0.0
            found[str(context_id)] = Note(
                context_id=str(context_id),
                body=str(entry.get("body", "")),
                updated_at=updated_at or time.time(),
            )
        self.notes = found

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 2,
                "notes": {
                    note.context_id: {
                        "body": note.body,
                        "updated_at": note.updated_at,
                    }
                    # An empty scratchpad is not worth a line in the file, and
                    # writing one back is how a context that was never typed in
                    # ends up looking like it was.
                    for note in self.notes.values()
                    if not note.is_empty
                },
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("could not write %s: %s", self.path, exc)

    def get(self, context_id: str = GLOBAL) -> Note:
        """The scratchpad for a context, or the global one.

        Always a note: there is nothing to create, so an untyped-in context has
        an empty scratchpad rather than no scratchpad.
        """
        note = self.notes.get(context_id)
        if note is None:
            note = Note(context_id=context_id)
            self.notes[context_id] = note
        return note

    def body(self, context_id: str = GLOBAL) -> str:
        return self.get(context_id).body

    def set_body(self, context_id: str, body: str) -> Note:
        """Write a scratchpad, if it says anything new.

        Called from an autosave, so the no-op guard is what stops a timer that
        fires on an unchanged buffer rewriting the file.
        """
        note = self.get(context_id)
        if body == note.body:
            return note
        note.body = body
        note.updated_at = time.time()
        self.save()
        return note

    def clear(self, context_id: str = GLOBAL) -> None:
        self.set_body(context_id, "")

    def forget(self, context_id: str) -> None:
        """Drop a context's scratchpad, for when the context itself is gone."""
        if self.notes.pop(context_id, None) is not None:
            self.save()

    def available(self, context_id: str | None = None) -> list[str]:
        """Which scratchpads the settings allow, in the order they are offered.

        The one place both settings are read, so every view agrees about what
        exists. Global comes first and stays first: it is the one that is always
        there, so a switch that puts it on the left keeps its buttons in the
        same place as you move between contexts, rather than shuffling them.
        """
        from context.state import settings

        live = settings.current()
        if not live.scratchpad:
            return []
        found = []
        if live.scratchpad_global:
            found.append(GLOBAL)
        if live.scratchpad_per_context and context_id:
            found.append(context_id)
        return found

    def preferred(self, context_id: str | None = None) -> str:
        """Which scratchpad a view should open on.

        Where you are, when there is a scratchpad for it. Kept apart from the
        order the two are offered in, so the buttons can sit still while the
        one you land on still follows the context.
        """
        offered = self.available(context_id)
        if context_id and context_id in offered:
            return context_id
        return offered[0] if offered else GLOBAL

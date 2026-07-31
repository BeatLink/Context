"""How a context arranges its windows.

A layout is a list of slots, each a rectangle in fractional monitor coordinates
(0.0–1.0). Resources are assigned to slots by index, so the layout describes shape
while the resource list describes content.

Fractions rather than pixels, so a layout is portable between monitors.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Slot:
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, raw: dict) -> "Slot":
        def num(key: str, fallback: float) -> float:
            try:
                value = float(raw.get(key, fallback))
            except (TypeError, ValueError):
                return fallback
            return min(1.0, max(0.0, value))

        return cls(
            x=num("x", 0.0),
            y=num("y", 0.0),
            width=num("width", 1.0) or 1.0,
            height=num("height", 1.0) or 1.0,
        )

    @property
    def is_full(self) -> bool:
        return (
            abs(self.x) < 1e-6
            and abs(self.y) < 1e-6
            and abs(self.width - 1.0) < 1e-6
            and abs(self.height - 1.0) < 1e-6
        )


@dataclass
class Layout:
    slots: list[Slot] = field(default_factory=list)

    def to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.slots]

    @classmethod
    def from_list(cls, raw: object) -> "Layout":
        if not isinstance(raw, list):
            return cls()
        return cls(slots=[Slot.from_dict(s) for s in raw if isinstance(s, dict)])

    def slot_for(self, index: int) -> Slot:
        """The slot a resource at `index` should occupy, full screen if unset."""
        if 0 <= index < len(self.slots):
            return self.slots[index]
        return Slot()

    def resized(self, count: int) -> "Layout":
        """Grow or shrink to hold exactly `count` slots, keeping what fits."""
        if count <= 0:
            return Layout()
        if len(self.slots) == count:
            return Layout(slots=list(self.slots))
        return preset_for(count)


# Named arrangements, keyed by how many windows they hold. These are what the
# editor offers as starting points; slots can then be dragged individually.
PRESETS: dict[str, list[Slot]] = {
    "maximised": [Slot()],
    "side-by-side": [
        Slot(0.0, 0.0, 0.5, 1.0),
        Slot(0.5, 0.0, 0.5, 1.0),
    ],
    "stacked": [
        Slot(0.0, 0.0, 1.0, 0.5),
        Slot(0.0, 0.5, 1.0, 0.5),
    ],
    "main-and-side": [
        Slot(0.0, 0.0, 0.65, 1.0),
        Slot(0.65, 0.0, 0.35, 1.0),
    ],
    "three-columns": [
        Slot(0.0, 0.0, 1 / 3, 1.0),
        Slot(1 / 3, 0.0, 1 / 3, 1.0),
        Slot(2 / 3, 0.0, 1 / 3, 1.0),
    ],
    "main-and-stack": [
        Slot(0.0, 0.0, 0.6, 1.0),
        Slot(0.6, 0.0, 0.4, 0.5),
        Slot(0.6, 0.5, 0.4, 0.5),
    ],
    "grid": [
        Slot(0.0, 0.0, 0.5, 0.5),
        Slot(0.5, 0.0, 0.5, 0.5),
        Slot(0.0, 0.5, 0.5, 0.5),
        Slot(0.5, 0.5, 0.5, 0.5),
    ],
}

PRESET_LABELS: dict[str, str] = {
    "maximised": "Maximised",
    "side-by-side": "Side by side",
    "stacked": "Top and bottom",
    "main-and-side": "Main and side",
    "three-columns": "Three columns",
    "main-and-stack": "Main and stack",
    "grid": "Grid",
}


def preset_for(count: int) -> Layout:
    """A sensible default arrangement for `count` windows."""
    by_count = {
        1: "maximised",
        2: "side-by-side",
        3: "main-and-stack",
        4: "grid",
    }
    name = by_count.get(count)
    if name:
        return Layout(slots=list(PRESETS[name]))

    # More windows than a preset covers: lay them out in as square a grid as fits.
    columns = 1
    while columns * columns < count:
        columns += 1
    rows = (count + columns - 1) // columns
    slots = []
    for index in range(count):
        row, column = divmod(index, columns)
        slots.append(
            Slot(
                x=column / columns,
                y=row / rows,
                width=1 / columns,
                height=1 / rows,
            )
        )
    return Layout(slots=slots)


def split_directions(slots: list[Slot]) -> list[str]:
    """Where each window goes relative to the one before it.

    A tiling compositor cannot be told "put this rectangle at these
    coordinates"; it is told which side of the current window to open the next
    one on. Comparing each slot to its predecessor recovers that: further right
    means a vertical split, further down a horizontal one.

    The first window has no predecessor, so it gets no direction.
    """
    directions: list[str] = []
    for index in range(1, len(slots)):
        previous, current = slots[index - 1], slots[index]
        moved_right = current.x > previous.x + 1e-6
        moved_down = current.y > previous.y + 1e-6
        if moved_right and moved_down:
            # Diagonal, as in the last cell of a grid: the horizontal split is
            # the one that produced the new row, so favour it.
            directions.append("d" if current.height < previous.height else "r")
        elif moved_right:
            directions.append("r")
        elif moved_down:
            directions.append("d")
        else:
            # Same origin — a full-screen slot, or one the user dragged on top of
            # another. Opening to the right keeps it visible rather than stacked.
            directions.append("r")
    return directions


def snap(value: float, step: float = 0.05) -> float:
    """Round a fraction to the nearest step, so dragging lands on clean edges."""
    return min(1.0, max(0.0, round(value / step) * step))

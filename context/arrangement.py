"""How a context spreads across the screens it finds.

A context owns one workspace per screen, not one overall. Which screen a window
lands on is part of the arrangement, and the arrangement depends on how many
screens are attached — a laptop on its own and the same laptop docked to two
monitors want genuinely different placements, not one stretched to fit.

So a context stores an `Arrangement` per screen *count*: one for undocked, one
for a single external, one for two. Plugging a monitor in switches to the
arrangement for that many screens, and unplugging switches back — both are
remembered rather than one overwriting the other.

Screens are numbered, not named. A layout keyed by `HDMI-A-1` is worthless the
day the cable moves to another port, and the same arrangement should apply at
the desk and in the office. Screen 0 is where the launcher is; the rest follow
the compositor's order, which is left to right for a normal side-by-side setup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .layout import Layout, preset_for

# More than this and the per-screen editor stops being readable, and nobody is
# arranging windows by hand across that many anyway.
MAX_SCREENS = 4


@dataclass
class Arrangement:
    """Where a context's windows go, for one number of attached screens.

    `screens` is a layout per screen, in order. `assignments` maps each resource
    index to the screen it belongs on, so the resource list stays a flat list of
    what a context contains and this says where each item goes.

    A resource with no assignment lands on screen 0, which is what makes a
    single-screen arrangement identical to the old single-layout form.
    """

    screens: list[Layout] = field(default_factory=lambda: [Layout()])
    assignments: dict[int, int] = field(default_factory=dict)

    @property
    def screen_count(self) -> int:
        return max(1, len(self.screens))

    def screen_for(self, index: int) -> int:
        """Which screen resource `index` belongs on."""
        wanted = self.assignments.get(index, 0)
        return wanted if 0 <= wanted < self.screen_count else 0

    def indices_on(self, screen: int) -> list[int]:
        """Resource indices on `screen`, in order."""
        return [i for i in sorted(self.assignments) if self.screen_for(i) == screen]

    def layout_for(self, screen: int) -> Layout:
        if 0 <= screen < len(self.screens):
            return self.screens[screen]
        return Layout()

    def grow_to(self, screens: int) -> None:
        """Make sure this arrangement actually has `screens` screens.

        An arrangement loaded from a single-screen context has one layout, so
        assigning a window to screen 2 clamped straight back to screen 1 and
        the move silently did nothing. Anything that edits an N-screen mode has
        to grow the arrangement to N first.
        """
        while len(self.screens) < max(1, screens):
            self.screens.append(Layout())

    def assign(self, index: int, screen: int) -> None:
        # Grow rather than clamp: being asked for a screen that does not exist
        # yet means the arrangement is behind the mode being edited, not that
        # the caller is wrong.
        self.grow_to(screen + 1)
        self.assignments[index] = max(0, min(screen, self.screen_count - 1))

    def healed(self, count: int) -> tuple["Arrangement", list[str]]:
        """Repair against a resource count, the way `Layout.healed` does.

        Every resource must be on some screen and every screen's layout must
        hold exactly the resources assigned to it, or launching tiles into
        nonsense. Assignments to screens that no longer exist come back to
        screen 0 rather than being dropped, since the window still has to go
        somewhere.
        """
        problems: list[str] = []
        screens = list(self.screens) or [Layout()]

        assignments = {}
        for index in range(count):
            wanted = self.assignments.get(index, 0)
            if not isinstance(wanted, int) or not 0 <= wanted < len(screens):
                if index in self.assignments:
                    problems.append(f"sent window {index + 1} to a screen that is gone")
                wanted = 0
            assignments[index] = wanted

        healed_screens = []
        for screen in range(len(screens)):
            on_screen = sum(1 for s in assignments.values() if s == screen)
            layout, screen_problems = screens[screen].healed(on_screen)
            problems.extend(f"screen {screen + 1} {p}" for p in screen_problems)
            healed_screens.append(layout)

        return Arrangement(screens=healed_screens, assignments=assignments), problems

    def to_dict(self) -> dict:
        return {
            "screens": [layout.to_list() for layout in self.screens],
            # Keys are stringified: JSON object keys are strings either way, and
            # writing them out that way keeps the file honest about it.
            "assignments": {str(k): v for k, v in sorted(self.assignments.items())},
        }

    @classmethod
    def from_dict(cls, raw: object) -> "Arrangement":
        if not isinstance(raw, dict):
            return cls()
        screens = [Layout.from_list(entry) for entry in raw.get("screens") or []]
        assignments: dict[int, int] = {}
        for key, value in (raw.get("assignments") or {}).items():
            try:
                assignments[int(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return cls(screens=screens or [Layout()], assignments=assignments)

    @classmethod
    def spread(cls, count: int, screens: int) -> "Arrangement":
        """A starting arrangement: `count` resources dealt across `screens`.

        Dealt rather than piled onto the first screen, since someone who has
        just plugged in a second monitor wants their windows to use it.
        """
        screens = max(1, min(screens, MAX_SCREENS))
        assignments = {index: index % screens for index in range(count)}
        layouts = [
            preset_for(sum(1 for s in assignments.values() if s == screen))
            for screen in range(screens)
        ]
        return cls(screens=layouts, assignments=assignments)

    @classmethod
    def from_layout(cls, layout: Layout, count: int) -> "Arrangement":
        """The single-screen form a context had before it could span."""
        return cls(screens=[layout], assignments={i: 0 for i in range(count)})

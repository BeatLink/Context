# Context — working notes

A context-oriented desktop shell in Python + GTK4/libadwaita. See
[README.md](README.md) for the concept and [ROADMAP.md](ROADMAP.md) for direction.

## Environment

`gi` is **not** in the ambient system Python. Everything runs through the dev shell:

```sh
cd /Storage/Files/Projects/Coding/Context
nix develop
python3 -m context
```

`nix shell nixpkgs#python313Packages.pygobject3` does **not** work — it doesn't set
`PYTHONPATH`. Use `nix develop`, or `python313.withPackages` for one-offs.

`nix develop --command` resets the cwd to the primary working directory, so run it
from this repo or pass absolute paths. Scripts run from elsewhere need
`PYTHONPATH=/Storage/Files/Projects/Coding/Context`.

## Code style

- **No comments** unless they explain a non-obvious *why*. Existing comments all
  earn their place (e.g. why a reused workspace returns early). Don't narrate code.
- Type-annotate signatures; `from __future__ import annotations` at the top.
- Dataclasses for data, `Protocol` for interfaces.
- Match the surrounding file — it's small and consistent.

## Architecture

Two extension points, both the same shape — a Protocol plus a registry with
`detect()`, and a Null implementation so calling code never branches on absence:

- **`backends/`** — where a context lives (workspace create/find/switch). Done.
- **Resource adapters** — how an app opens to a resource. Not built yet; this is
  next and is the point of the project.

When adding either, follow `backends/base.py`: a Protocol, a null/generic fallback,
and registration in `__init__.py`.

## Invariants

- **Workspace identity is the handle, never the title.** Contexts store
  `workspaces: {backend: handle}`. Matching by title orphans workspaces on rename —
  this was a real bug, don't reintroduce it.
- **Store loading is additive.** `Context.from_dict` filters to known fields, so old
  JSON keeps loading. Add fields with defaults; don't require them.
- **Reopening an existing context must not relaunch apps.** If the workspace exists,
  switch and return.
- **A failing app must not break the launch.** Collect failures into
  `LaunchResult.failed` and report; never raise out of `launch_context`.

## Working rules

**Track the work in a todo list.** Requests arrive faster than they are finished
and it is easy to lose one silently. Put every request on the list as it comes
in, keep one item in progress, and mark items done as they land.

Every change should land as a complete unit:

1. **Add or update tests.** A fix without a test comes back. Bugs that reached
   the running desktop especially — several tests exist only to pin those down.
2. **Update the roadmap.** Mark what is done, add what the change revealed.
3. **Commit with a message that explains the why**, not just the what. The
   interesting part is usually the constraint discovered, not the diff.

## Documentation style

The README describes what the software does, for someone meeting it for the
first time. It is not a changelog and not a record of decisions.

- Write features as they are, not as they became. No "rather than", "previously",
  "this used to", or "the reason for this is".
- Leave out the alternatives that were rejected and the bugs that shaped a
  design. Those belong in commit messages, where the history is the point.
- A reader has no memory of earlier versions and does not need one.

Design rationale still belongs somewhere — this file for constraints worth
remembering, ROADMAP.md for what is planned and why, commit messages for what
changed and what forced it.

## Theming

Context draws its own app tiles and layout preview, so libadwaita has no styling
for them. Colours live in `context/theme.py` and are read from
`$XDG_CONFIG_HOME/context/theme.json`, falling back to the defaults. Never
hard-code a colour in a widget or a Cairo call — add it to `Theme` instead, so
both the stylesheet and the drawing code get it from the same place.

## Testing

```sh
nix develop
python3 -m pytest tests/ -q          # logic only
xvfb-run -a python3 -m pytest tests/ # including the GUI tests
```

`tests/conftest.py` provides `FakeBackend`, an in-memory window manager that
records the calls made to it — the launch tests assert on the *sequence*
(switch, preselect, launch, preselect, launch), which is what tiling depends on.
`isolated_store` is autouse and redirects `XDG_DATA_HOME`, so no test can touch
real contexts.

GUI tests are skipped without a display rather than failing. They must be
exercised, not just imported.

Traps found the hard way:

- **`GDK_BACKEND=broadway` is unavailable** in this GTK4 build. Use Xvfb.
- **`Adw.Application` hands off over D-Bus** to an already-running instance with the
  same app ID and exits immediately — silently doing nothing. Wrap GUI tests in
  `dbus-run-session`, or use a distinct application ID.
- **`do_activate` builds the window**, so a test's own `activate` handler runs while
  `app.window` is still `None`. Call `app.do_activate()` first.
- **`Gtk.SearchEntry` debounces `search-changed`** (~150ms). Reading list state
  immediately after `set_text()` reads the *old* state. Wait a tick.
- Isolate state with `XDG_DATA_HOME=<tmpdir>`, and force the no-op WM with
  `CONTEXT_BACKEND=none`.

### Touching the live desktop

The Cinnamon backend mutates the real session. **Snapshot and restore** around any
test that runs it — the user has their own named workspaces:

```sh
gsettings get org.cinnamon.desktop.wm.preferences workspace-names
gsettings get org.cinnamon.desktop.wm.preferences num-workspaces
wmctrl -d | awk '/\*/{print $1}'
```

Prefer a fake app for launch tests (a `.desktop` whose `Exec` touches a file) over
launching real applications, and point `XDG_DATA_DIRS` at it.

## Gotchas

- **`Gio.DesktopAppInfo.new` raises `TypeError`** on a missing entry rather than
  returning `None`. Catch both; a stale app ID otherwise crashes the launcher.
- **Never let a launched app inherit `LD_PRELOAD`.** The sidebar re-execs with
  gtk4-layer-shell preloaded, and injecting that into Firefox segfaults it. Launch
  through `child_env()` — both `subprocess` and `Gio.AppLaunchContext`, since Gio
  copies the process environment rather than taking a dict.
- **Firefox exits non-zero and silently** when its profile is still held by a
  closing instance. `Popen` without checking the status makes a failed launch look
  like a success. Status 1 means the profile is busy; other codes are crashes.
- **Nix `''` strings**: escape shell `${...}` as `''${...}`. `$\{` leaks a literal
  backslash into the output.
- **Hyprland cannot be nested** in an X11 or Cinnamon-Wayland session — every host
  fails on protocol versions or GPU access (table in README). It needs a spare VT or
  a VM. Keep logic testable without a live Hyprland; the `hyprctl` stub approach in
  the scratchpad works well for this.
- `GLib.get_real_name()` returns the literal string `"Unknown"`, not empty, when
  GECOS is unset.

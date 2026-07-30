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

## Testing

There is no test suite yet. Verification so far has been ad-hoc scripts in the
scratchpad run against a real GTK render — worth turning into real tests.

GUI code must be exercised, not just imported. Under this X11 session:

```sh
Xvfb :99 -screen 0 1280x1024x24 &
DISPLAY=:99 GDK_BACKEND=x11 python3 <script>
```

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

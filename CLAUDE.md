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

- **`backends/`** — where a context lives (workspace create/find/switch).
  Hyprland is the only one; anything else falls back to `NullBackend`.
- **`adapters/`** — how an app opens to a resource. Firefox, VS Code and
  terminals are adapted; everything else uses `GenericAdapter`.

Isolation cuts across adapters rather than being one: `adapters.isolating()` is
a context manager the launcher wraps each launch in, and `child_env()` /
`child_command()` consult it. It is a `ContextVar` rather than an argument so a
new adapter gets isolation without knowing it exists — and not a module global,
because launches run on worker threads and two contexts can start at once.

When adding either, follow `backends/base.py`: a Protocol, a null/generic fallback,
and registration in `__init__.py`.

**Hyprland is the only backend, deliberately.** Cinnamon was dropped rather than
maintained: it could not preselect a split or resize a tiled window, and it could
only remove its *last* workspace, so closing a context from the middle renumbered
every other context's handle. Supporting it capped every feature at what the
weaker backend could express. Don't add a second window manager back without a
reason stronger than "it would also work".

## Invariants

- **A context owns one workspace per screen, not one.** `handles_for()` is the
  set; `handle_for(backend, screen)` is one of them. Anything asking "is it
  open", "is it active", or closing has to work over the whole set — a context
  spanning two screens is open when *any* of them has windows.
- **`monitors.ordered()` is the only place a screen number becomes a monitor.**
  Contexts say "screen 2" and never name a display. Anything that needs the
  physical monitor goes through there, so the mapping is one setting rather
  than scattered assumptions — and never derived from focus, which would make
  screen 2 a different monitor depending on where the pointer was.
- **Arrangements are keyed by screen count, not monitor name.** A layout keyed
  by `HDMI-A-1` is worthless the day the cable moves ports. Docked and undocked
  are separate arrangements and both survive.
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

**Docs, then source, then experiment — in that order. Never assume.**

1. **The documentation**, for what is meant to exist. `wiki.hypr.land` is
   JS-rendered, so `WebFetch` returns navigation rather than content; search it
   or read the underlying markdown instead of concluding it says nothing.
2. **The source**, for what actually exists. `nix eval --raw nixpkgs#<pkg>.src`
   puts it on disk. This is the definitive answer and is usually faster than
   arguing with a wiki: `CWorkspace` holding one `PHLMONITORREF` settles
   "can a workspace span monitors" in one grep.
3. **A live experiment**, for what actually happens. Documented behaviour is
   sometimes not the behaviour — a `match:workspace` window rule was accepted
   without error and never applied.

Skipping to step 3 makes a guess look like a finding. Skipping step 3 makes a
finding look like a guess. Both have cost real time here.

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

Context is themed the way waybar or swaync are: one built-in stylesheet, with
`$XDG_CONFIG_HOME/context/style.css` loaded over it and watched for changes.
There is no scheme setting and no preset system, deliberately — a look is a
style.css someone (or something — matugen, a home-manager template) writes.

The contract, and the constraints holding it up:

- Every colour is published as `@define-color ctx_<field>` and the built-in
  rules only reference the names. A redefinition in style.css overrides the
  built-in definition across providers — measured on a real widget in
  `test_a_user_redefinition_reaches_the_widgets`, since the whole design rests
  on it.
- The user provider sits at `STYLE_PROVIDER_PRIORITY_USER + 1`: the desktop
  theme's own user CSS arrives at USER (800), and Context's colours have to be
  the last word on its own windows.
- Cairo drawing (app tiles, layout preview) cannot read CSS, so `Theme.load`
  parses the `@define-color ctx_*` lines out of style.css itself. Never
  hard-code a colour in a widget or a Cairo call — add a field to `Theme` so
  the stylesheet and the drawing stay one palette.
- Styles revalidate on the next frame, not at the reload call. A test that
  reads a colour back synchronously after `reinstall()` reads the old value
  and proves nothing; present the window and wait.

Transparency is the alpha of `ctx_surface` — GTK's `alpha()` takes a literal
rather than a named colour, so there is nowhere else to put it. The full-screen
views wear `.ctx-solid` and paint `@ctx_surface_solid`, which `_defines()`
derives from the surface with the alpha taken off: a haze over the whole output
is not the same thing as a translucent strip at the edge.

**Do not add a light/dark scheme system back.** One existed and was removed:
three attempts at a built-in light mode failed while libadwaita resolved
light/dark before the application got a say, and the scheme machinery that
replaced it (portal reading, per-scheme palettes, a settings dropdown) was a
theming system nothing else on a Hyprland desktop has — waybar ships one look
and a CSS file, and so does Context. A light look is a style.css.

If theming is ever debugged again: verify by sampling pixels from a
screenshot, never by looking up a colour. A colour can resolve correctly and
be painted over, which is what made two light-mode attempts look like they had
worked.

## Two views of the same thing

The sidebar and the overview list the same contexts and the same applications,
so they share their pieces rather than each growing their own — `rows.py` holds
`ContextRow`, `AppRow`, `app_tile` and `context_for_app`, and both views build
from them. They drifted apart once: the overview could open a context but not
edit, close or forget one, and its Enter key did nothing where the sidebar's
started something. **A new handle on a context goes in the row, not in a view.**

Neither view owns the editor. It is a layer-shell overlay holding the keyboard
exclusively, and so are the overview and the pickers — stacked, the top one is
typed into while the bottom one still covers the screen. So the overview closes
and hands the context to `app.edit_context`, which routes to the launcher.

Closing a context is the exception that stays put: it is housekeeping done
while you carry on choosing, so the overview refreshes rather than dismissing.

## Slots are fractions of the windows, not of the screen

`tiled_box()` is the rectangle the windows on a workspace span, and every slot
— captured or compared — is a fraction of *that*. Not of the monitor: the
bars, the sidebar, the compositor's gaps and hyprbars' titlebars all sit
between the panel and the windows, so a maximised window measured against the
screen came out at 0.006 from the left and 0.911 tall. It never equalled the
`0,0,1,1` it was launched from, and every context read as drifted the moment
anything reserved a different amount of space — collapsing the sidebar was
enough. The launch path already worked in these terms (`apply_ratios`
proportions the area the windows occupy), so this is the two ends agreeing.

The consequence to know: **a lone tiled window cannot drift in position** — it
*is* the whole box wherever the compositor put it. Only its count, or the
proportions between two or more windows, can change.

## What is live, in one pass

`launcher.read_live_state` answers everything the list needs — which contexts
are open, which is focused, which have drifted, and which windows belong to no
context — from one `geometry_by_handle` call plus the two `open_state` already
made. It runs on the poll timer, so a query per question is subprocess work on
the GTK main loop; asking `has_drifted` per context was a `hyprctl clients`
each, every couple of seconds.

The no-context is a `Context` that is never stored: `loose_context()` builds
one around the homeless windows and carries them on `ctx.windows`, so the views
show and act on it with the row they already have. `is_no_context(ctx)` is the
guard every action needs — it has no workspace to switch to, no definition to
edit, and saving it means *becoming* a context rather than capturing one.

## Declared contexts are seeds

`$XDG_CONFIG_HOME/context/contexts.json` is what something else — the NixOS
module, a dotfile repo — writes to declare contexts. `ContextStore.seed_declared`
takes each one in *once*, recording its id in `uistate` under
`seeded_contexts`. That record is the whole design: without it a forgotten
context comes back at the next start, and an edited one is overwritten by the
declaration. Ids are derived from the title (`declared:work-on-context`) so the
same declaration is the same context across machines.

## Notifications, not toasts

What the launcher reports goes to the desktop's notification daemon through
`notify.send`. Toasts were drawn over the launcher's own list, which is only on
screen while it is expanded — Context spends most of its life as a rail, so a
failed launch reported itself to nobody. `notify.py` owns the `notifications`
setting, and a message with a button registers a `Gio.SimpleAction` for it,
since `Gio.Notification` takes an action name rather than a callback. Keys are
per message, not per occurrence: sending "launch" again replaces what is up.

## Keyboard focus in the sidebar

The layer is `KeyboardMode.ON_DEMAND`, which is the protocol's "let the user
focus and unfocus this the way they would an ordinary window". Clicking in
gives it the keyboard, and it does not take focus on map — measured: starting
Context leaves the focused window focused.

**Clicking away does *not* give the keyboard back on Hyprland.** Its click
path only refocuses when the window under the click *changes*, and a layer
holding the keyboard does not count — the old window is still recorded as
focused, so clicking back into it does nothing and typing stays in the
sidebar until some other window is focused first (read from the 0.56 source:
`processMouseDownNormal` skips `refocus()` when `focusState()->window() == w`).
The sidebar therefore releases the keyboard itself when the pointer leaves
while it still holds it, guarded so an open popover postpones the release —
see the next paragraph for why that guard exists.

**Never drop the keyboard while a popover is up.** An earlier design released
on every pointer-leave: opening a dropdown sends the parent a synthetic
pointer-leave, the keyboard was dropped, and the popover dismissed itself a
frame later. `_maybe_release_keyboard` waits for the popover to close and only
then releases, if the pointer is still outside.

**Releasing the layer's keyboard mode alone does not revive typing.** Two
separate traps, both measured in the wild (see DankMaterialShell#2561 for the
same bug in another shell):

- Keyboard interactivity is double-buffered protocol state. Setting NONE and
  ON_DEMAND back-to-back lands in one commit and collapses to no change; the
  compositor never sees a release. `release_focus` commits the NONE first and
  restores ON_DEMAND on the frame after.
- Even a committed release is answered by Hyprland re-reporting the window as
  active *without re-sending `wl_keyboard.enter`* — the seat routes typing
  nowhere. `hand_keyboard_back` focuses the most recent window explicitly,
  which is the recovery clicking another window performs. Every path that
  gives the keyboard up ends with it: Escape, opening a context, the pointer
  leaving, an editor or picker overlay closing.

## More than one launcher

`monitor = "*"` puts a launcher on every screen, because a layer surface
belongs to exactly one output. `app.launchers` is all of them; `app.window` is
the primary and is what the editor and refreshes go through.

**Anything stored once must be applied to all of them.** Collapsing shipped
broken twice for this reason: the collapsed flag is a single value in
`ui.json`, but the toggle applied it to whichever window was clicked, so the
two disagreed and whichever restarted last decided what the setting had been.
Settings changes fan out the same way.

Every test built one `LauncherWindow` directly, which is why none of it was
caught — see the multi-launcher block in `test_window.py`.

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

- **Assert geometry, not just widget state.** "The stack switched to the rail"
  is not "the sidebar is 36px wide". A rail that came out at 44px when asked
  for 32 passed every test. Measure the window.
- **A stub adapter that never opens a window costs 10s per launch.** Every
  launch waits `WINDOW_TIMEOUT` for a window that is not coming. `stub_adapters`
  adds one to the workspace being launched into; without that the suite took
  225 seconds instead of 14.

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

The Hyprland backend drives the real session. Prefer `FakeBackend` for anything
that would create or close a workspace. Where a real one is unavoidable, use a
fake app for launch tests (a `.desktop` whose `Exec` touches a file) rather than
launching real applications, and point `XDG_DATA_DIRS` at it.

## Gotchas

- **Nothing slow may run on the GTK main loop.** Launching a context starts apps
  one at a time and waits up to 10s for each to map, which froze the launcher for
  the whole run. `app.launch_context` does the work on a thread and returns the
  result through `GLib.idle_add`; nothing in `_launch_worker` may touch GTK.
- **Never wait on a launched app to exit.** `subprocess.run` on Firefox looked
  safe because an already-running browser makes the invocation hand its URL over
  and exit within a moment. With no browser running the invocation *becomes* the
  browser, and the launcher hung for as long as it lived. Spawn with `Popen`,
  watch for `STARTUP_GRACE`, and treat "still running" as success.
- **The process you spawn is not the process that owns the window.** Verified on
  a live session: the spawned Firefox had exited before its window appeared, and
  the window's pid was parented to the session. VSCodium behaves the same. Pid
  matching cannot be the basis of window tracking — see ROADMAP §3.
- **`--class` does not set the Wayland app_id** for Electron apps; it is an X11
  flag. Measured: VSCodium reports `codium` with `--class=ctx-test-class`, even
  with a separate `--user-data-dir` forcing a genuinely new process. Firefox is
  the exception, via `MOZ_APP_REMOTINGNAME` — measured working.
- **`Gio.DesktopAppInfo.new` raises `TypeError`** on a missing entry rather than
  returning `None`. Catch both; a stale app ID otherwise crashes the launcher.
- **`XDG_RUNTIME_DIR` cannot be isolated.** The Wayland socket lives in it, so
  an app given a private one dies with "cannot open display". Isolation is the
  private D-Bus session and nothing else — that is the channel hand-off
  actually travels over. Measured after building it the other way first.
- **Scrub the launcher's own environment before launching anything.**
  `child_env()` exists for this and its list only grows. `ELECTRON_RUN_AS_NODE`
  is the one that cost real time: editors set it for their integrated
  terminals, so a Context started from one passes it to every application it
  launches, and every Electron application dies with "Cannot find module
  'electron'" while the launch reports success. `Gio.AppLaunchContext` copies
  the process environment rather than taking a dict, so removing a variable
  needs `unsetenv`, not merely setting the others.
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
- **No popover-based controls on an overlay.** A `Gtk.DropDown` in the editor
  or the adopt window opens its list and then throws the click away: the popup
  closes with the old value still selected, so both layout dropdowns appeared
  to do nothing. Reported from the live session, not reproducible offscreen —
  the model changes fine when the selection is set in code. Use
  `widgets.SegmentedChoice` (buttons in the surface itself) for anything an
  overlay has to choose between. The sidebar is not affected; the settings
  page's combos work.
- **A GTK label shows what it is given.** `markup_escape_text` on a title that
  is *not* parsed as markup spells the entities out — every context row but the
  current one read "Review todos &amp; notes", because only the active row is
  bolded and therefore only it parses markup. Escape where markup is used and
  nowhere else.
- **The expanded sidebar cannot see the cursor in its own gaps.** It floats 8px
  off the edges, so the pointer crossing that gap is a pointer-leave with the
  cursor visibly still on the sidebar. Hover-expansion is held open by a *zone*
  — the sidebar's width plus its margins, from the docked edge — polled from
  `backend.cursor_position()`, with `collapse_delay_ms` before it retracts.
- `GLib.get_real_name()` returns the literal string `"Unknown"`, not empty, when
  GECOS is unset.

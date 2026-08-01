# Roadmap

Ordered roughly by dependency. The near-term goal is not more UI — it's making a
context actually *be* something, which means resources and app integration.

## Now

### 1. Resource adapters — *done for Firefox*

The core gap. A context is currently `apps: ["firefox.desktop"]` — a list of app
IDs, which cannot express "Firefox at reddit" or "VS Code at this workspace". That
expressiveness is the entire premise of the project.

Replace the flat app list with resources, each handled by a per-app adapter:

```python
resources: [
  {app: "firefox", profile: "ctx-<slug>", urls: [...]},
  {app: "vscode",  workspace: "/path/to/x.code-workspace"},
  {app: "foot",    cwd: "/path"},
]
```

Adapters share one interface (mirroring `backends/base.py`): given a resource,
produce a launch. A generic adapter falls back to plain desktop-entry launch for
apps with no special handling, so nothing regresses.

Adapter difficulty, from what's been verified:

| App | Approach | Difficulty |
| --- | --- | --- |
| Terminals | `cwd` + optional command | trivial |
| VS Code | `code <folder\|.code-workspace>` | trivial |
| Firefox | per-context profile + URL list | easy, see below |
| Chromium | `--user-data-dir` per context | easy |
| Electron apps | inconsistent; often no useful CLI | hard |
| Everything else | generic desktop-entry launch | n/a |

Keep `apps: list[str]` loading from old files (the store already filters unknown
keys, so migration is additive).

### 2. Adapters — *Firefox, VS Code and terminals done*

**Shipped.** Firefox takes URLs and chooses between a dedicated per-context
profile and the user's main profile. VS Code opens a folder, a file or a
`.code-workspace`. Terminals open at a directory and can run a command.

Each resource also carries compatibility switches — whether to force a new
window, and whether the app is single-instance — because how an app behaves when
already running differs per app and cannot be reliably detected. Tilix is the
example that made this necessary: it is D-Bus activated, so plain `tilix` raises
the window it already has and a context gets no terminal.

Remaining: a settings surface for these that is not per-resource guesswork, and
adapters for whatever else turns out to matter in use.

The profile-per-context design below is a workaround for Firefox having no
window-targeting flags. Once contexts track windows directly (item 3), dedicated
profiles become a choice about *isolation* — separate cookies and history — rather
than the only way for a context to own its window.

Verified on Firefox 153: one CLI call opens one window with all URLs as tabs.

```sh
firefox --profile <dir> --new-instance <url1> <url2> <url3>
# → 1 window, 3 tabs (confirmed via sessionstore: "window -> 3 tabs")
```

Use **one profile per context**. There are no window-targeting flags, so with a
shared profile `--new-tab` lands in whatever window was last focused — contexts
would interleave tabs and couldn't reliably reuse their own window. A profile per
context gives a separate instance, and with it:

- the context owns its window; no interleaving
- tab restore on reopen comes free from Firefox session restore
- ephemeral teardown is `rm -rf <profile>`
- separate cookie jars, so work and personal identities can differ per context
- a distinct PID per instance — which makes window→context matching *easy* for
  Firefox rather than hard

Costs: ~150–250MB RAM per live instance, no shared history/bookmarks unless seeded
or synced, and a fresh profile shows onboarding (suppress via seeded `user.js`).

Explicitly **not** doing an extension yet. It's only needed for tab groups within
one window, reading back live tab state, Firefox Containers (no CLI surface at all),
or sharing one process across contexts. All are v2, and an extension costs signing,
distribution, a native messaging host, and per-release breakage.

### 3. Window placement, and tracking by window rather than profile

`prepare_launch()` is currently a no-op — it relies on focus-then-launch, which is
racy: a slow-mapping app can land on whatever workspace you switched to since. Fix
properly with Hyprland workspace rules or workspace-bound `exec` dispatch.

**Track windows, not profiles.** Per-context Firefox profiles were never really
about identity; they were a workaround for Firefox having no window-targeting
flags. Tracking the *window* instead lets any window belong to a context whatever
spawned it, which dissolves several problems at once:

- the main-profile mode gets real per-context grouping, so shared addons, cookies
  and history stop costing tab isolation
- no profile lock contention, so no waiting for a closing instance to let go
- no duplicated profile storage per context
- windows a context didn't spawn can be adopted into it
- **single-instance apps stop silently failing.** Confirmed on a real Hyprland
  session: launching "Listen to Music" reported success and opened nothing, because
  Quod Libet was already running elsewhere and its desktop entry just focused the
  existing window. Launching by desktop entry cannot fix this — the context has to
  find the window and move it.

Hyprland has the pieces: `hyprctl clients -j` reports every window's address, pid,
class and workspace, and `dispatch movetoworkspacesilent address:0x…` moves a
specific one. The `Context` already carries a per-backend handle, so window
addresses fit the existing shape.

The hard part is the same one that makes `prepare_launch()` a no-op: **Wayland has
no general way to match a new window back to the launch that spawned it.**
`xdg-toplevel-tag-v1` exists for exactly this but is opt-in per app, so it can't be
relied on. Three approaches, none sufficient alone:

**Match on pid.** `hyprctl clients -j` reports a `pid` per window, so tracking the
pid of each launch and correlating looks like the obvious answer. Measured on a
live session, it does not hold for the apps that matter: the spawned Firefox had
exited entirely by the time its window existed, and the window's pid belonged to a
process parented to the session rather than to Context. VSCodium was the same.
Both re-exec or hand off to a running instance, so the process started is not the
process that owns the window. Pid matching works for a single-process app that
owns its own window; it fails on exactly the multi-process and single-instance
apps that need it most. Worth keeping as a fast path, never as the mechanism.

**Launch each window under its own class.** The strongest option where it works:
the window arrives already identifying itself, so there is nothing to correlate
and no race. A unique class also lets a Hyprland `windowrule` pin the window to
the context's workspace, which solves placement at the same time and survives an
app that takes half a minute to start. Measured on a live session:

| App | Mechanism | Result |
| --- | --- | --- |
| Firefox | `MOZ_APP_REMOTINGNAME=ctx-…` | **Works** — reports `class: ctx-probe` |
| VSCodium | `--class=…` | No effect; stays `codium` |
| VSCodium | `--class=…` with a separate `--user-data-dir` | Still `codium` |
| Electron generally | `--class=…` | X11-only flag; Wayland `app_id` comes from the desktop entry |

So it is per-app, not general. Firefox — the app the profile-per-context
workaround exists for — is the one that supports it, which makes it worth doing
even alone. Two caveats: a custom class breaks icon lookup and desktop-entry
association, so Context has to map it back for the bar and its own app grid; and
`MOZ_APP_REMOTINGNAME` forces a separate instance, so it does not by itself give
main-profile mode per-context windows.

**Watch `openwindow` on the event socket.** The general fallback for everything
that cannot name its own class. Hyprland announces each window as it maps, with
its address, class and title. Correlating "the next window of class X after I
launched X" is racy in theory and reliable in practice, since Context launches
one app at a time and waits. Note the event's address omits the `0x` prefix that
`hyprctl` requires.

**A Context Firefox addon.** The one case none of the above reaches is telling
two windows of the *same* browser apart: in main-profile mode every window shares
one process and one class, so there is nothing to distinguish them. An addon can
label a window on the browser's own terms and report back over native messaging,
which would give main-profile contexts real per-context grouping without a profile
each. It also unlocks capturing open tabs into a context (see Later). The cost is
a second codebase, a signed build, and an install step — so it should follow the
compositor-level work rather than replace it, and stay optional.

Do the first two together with placement: same IPC, same blocker, same session
needed.

Then: layout within a context (the "VS Code docked left, docs right" case), which
needs Hyprland IPC and is a reason to treat Hyprland as the real target.

### 5. Collapse the sidebar to a rail — *done*

A button in the header shrinks the sidebar, in one of three modes: to a rail of
icons, to a sliver, or not at all. A rail and never-collapse stay pinned to the
edge and keep reserving space; only hiding gives it back. The exclusive zone
follows the window size, so tiled windows reflow with no extra compositor work.
The state is kept in `$XDG_STATE_HOME/context/ui.json` and restored on start —
unless collapsing is off, in which case it is ignored rather than leaving the
sidebar shrunk with no button to grow it.

The rail lists every context rather than the current search results — there is
no search bar at rail width to explain why some are missing.

Still to do: a keybind for the toggle, and hover-to-peek so a context can be
identified without expanding.

### 5b. Multiple monitors — *done*

A context owns one workspace per screen rather than one overall, and stores an
arrangement per screen *count*. Docked and undocked are different placements,
both remembered: laying a context out across two monitors does not destroy the
laptop-only version, and plugging the monitor back in restores it.

Screens are numbered rather than named. A layout keyed by `HDMI-A-1` would be
worthless the day the cable moved to another port, and the same two-screen
arrangement should apply at the desk and in the office. Screen 0 is the focused
one; the rest follow the compositor's left-to-right order.

Everything that was written against a single handle now works over the set:
a context is open when *any* of its screens has windows, active when you are
looking at any of them, and closing shuts all of them. Handles derive from the
primary (`ctx-work`, `ctx-work-s2`), so renaming a context still cannot orphan
a workspace.

Verified on a live two-monitor session: two workspaces created, bound to eDP-1
and HDMI-A-1, one window each, and closing shut both.

The editor shows one preview per attached screen, each at that monitor's own
aspect. Dragging a window off the side of one preview hands it to the next,
which is the same gesture as pushing it across the desk.

Known wrinkle, inherited rather than introduced: an empty named workspace
survives while it is a monitor's *active* workspace, so closing a spanning
context often leaves its handles in place until you look elsewhere. Harmless —
the handles are stable names, reopening reuses them, and `reconnect` drops them
on restart — but it means `workspace_removed` is usually False for a context
that spans.

**Screen identity is a setting, not a guess.** `settings.screen_order` names
which monitor is screen 1, screen 2 and so on, and `monitors.ordered()` is the
only place a screen number becomes a physical display. Contexts stay screen
agnostic: they say "screen 2" and never name a monitor, so moving a cable is
one change on the settings page rather than an edit to every context.

A configured monitor that is not plugged in is skipped rather than leaving a
gap, so unplugging the middle of three promotes the third to screen 2 instead
of the layout losing a screen. `max_screens` decides how many arrangements a
context can hold, and the editor can edit any of them — the two-screen layout
can be set up while undocked, which is the point of keeping them separate.

### 6. Launch apps into the current context

Launching an app outside Context puts it wherever the compositor decides, which
is how windows end up somewhere the context does not own. Context should be the
way apps are launched:

- a launcher — the app grid already exists in the editor, so this is mostly
  presenting it as its own view
- launching adds the app to the *current* context rather than a new one, and
  tiles it into the layout beside what is already there
- optionally, remember it: an app opened this way can be added to the context's
  definition so it comes back next time

This is what makes Context replace rofi rather than sit next to it.

### 3b. Isolated contexts — *done*

A context can be marked isolated: its applications launch under a private D-Bus
session, so they cannot see a running copy of themselves and cannot hand off to
it. Measured A/B on a live session, two terminals in one context:

| Context | Windows | Distinct pids |
| --- | --- | --- |
| Shared bus | 2 | **1** — one process owns both, unattributable |
| Isolated | 2 | **2** — each window is its own process |

That is the identity problem dissolving for the applications it covers: the
process Context spawns is the process that owns the window, so pid matching
works and `--new-window` is unnecessary.

**Only the bus is isolated.** Redirecting `XDG_RUNTIME_DIR` looks equally
correct — it is where unix sockets live — and is wrong: the Wayland socket lives
there too, so an application given a private one has no display and dies with
"cannot open display". Measured, after implementing it that way first. The bus
is the channel that actually carries hand-off.

Sharing the Wayland socket leaks nothing, because Wayland gives a client no way
to enumerate other clients.

It is off by default and off is the right default: two copies of an application
writing one database without knowing about each other is how data is lost, and
not knowing is exactly what isolation produces. Both the context and the
application have to agree — the per-resource `isolate` switch opts an
application out, since the application is what knows about its own store.

Still to do:

- **Per-context data directories**, so an application that keeps a shared
  database can be isolated safely rather than merely excluded. `HOME` cannot be
  moved wholesale without losing configuration, so this is per-adapter: Firefox
  already has `--profile`, Electron has `--user-data-dir`, most applications
  have nothing and would need `XDG_DATA_HOME` and `XDG_CONFIG_HOME` redirected
  with the consequences that brings.
- **Verify it against the remaining channels.** The bus covers GApplication and
  D-Bus activation. Applications that use an abstract socket or a lock file
  under their own data directory are untouched by this and still need the
  compatibility switches.

### 7b. A thumbnail switcher

The switcher is a list: icon, title, and which context a window belongs to.
Live previews would make picking between four windows of the same application
much faster, and Cinnamon's alt-tab had them.

**It is possible, and it is real work.** Hyprland implements
`hyprland-toplevel-export-v1`, which captures a *named toplevel* rather than the
whole output — this is what the desktop portal uses for single-window sharing.
So the frames are obtainable even for windows on another workspace, which
`wlr-screencopy` cannot do because it only captures outputs and an unfocused
workspace is not being rendered.

The cost is a Wayland protocol client. `hyprctl` does not expose capture, so
this means talking the protocol directly — pywayland, a buffer pool, and a
format conversion per frame — none of which the project has today. There is no
shortcut: cropping a screenshot only works for windows currently on screen,
which is exactly the case where a switcher is least needed.

Worth doing behind a setting rather than as a replacement, since a list is
faster to read for windows with distinct titles and costs nothing to render:

- **List** — icon, title, context. The default.
- **Thumbnails** — a grid of live previews.

Do it after window identity (item 3), which needs the same protocol-level work
and matters more.

### 8. Window management — *done*

Four things a context could not do, all of which needed the compositor rather
than the launcher:

**Move a window into another context.** `Super+Shift+M` picks a context and
sends the focused window there. `movetoworkspacesilent`, so moving a window out
of what you are working in does not drag you out with it. A context has to be
open to receive one — a named workspace does not exist until something is on it.

**Throw a window between a context's screens.** `Super+Ctrl+Left/Right`, staying
inside the context.

**Adopt loose windows.** `Super+Shift+O` lists every window belonging to no
context and offers each one a home.

**Capture what a context became.** `Super+Shift+C` reads the live window
positions back into the arrangement for the current screen count, so a context
tracks reality rather than staying a snapshot of the day it was written.
Resources are matched to existing ones by app id, so capturing does not throw
away the URLs or paths a window was opened with.

Plus fullscreen, maximise, float, tile, centre, group and ungroup, which are
Hyprland dispatchers routed through Context so its idea of what a context owns
stays in step.

Two things measured rather than assumed. `moveintogroup` only joins a neighbour
that is *already* a group and reports success when there is nothing to join, so
two ordinary windows never grouped — `moveintoorcreategroup` is the one that
works. And a window reports the monitor it is on by *id*, not name; taking the
monitor from the screen index instead put every captured slot at x=1.0 whenever
a context's screen 0 was not monitor 0.

**Screen assignment is honoured.** Screens are ordered by position, never by
focus. Sorting by focus meant "screen 2" was a different physical monitor
depending on where the pointer happened to be, so an app the user pinned to
their right-hand display opened on the left whenever they launched from there.

### 9. Settings — *page done, per-context visibility planned*

**Shipped.** A settings page reached from the launcher's header, covering the
sidebar edge, both widths, hover-to-expand and its delay, the backend, the
refresh interval and the log level. Settings live in
`$XDG_CONFIG_HOME/context/settings.json` and are hand-editable; environment
variables still override them for a single run. The page also lists where every
file lives and can write out the style file.

Changes apply as they are made. The widths take effect immediately; the edge,
the backend and the log level say that they apply on restart rather than
pretending otherwise.

**Still to do — a flag for every setting.** Three ways to set a value exist
already: the settings page, `settings.json`, and a handful of environment
variables. The environment variables are the odd one out — they cover only what
happened to need overriding during development, and their names do not follow
the settings they map to.

Replace them with a flag per setting, generated from the `Settings` dataclass so
the three can never drift: `--sidebar-width 420`, `--collapse-mode hidden`,
`--color-scheme dark`. A flag applies to the run it is given to, the same way
the environment variables do now, and `--help` becomes the list of what is
configurable. Keep the environment variables working, since they are documented.

This also gives the keybind commands somewhere to grow: `switch-window
--scope all` rather than a separate `switch-window-all`.

**Still to do — per-context visibility.** With a handful of contexts everything
can be listed; with fifty it cannot, and the alternative to this option is an
arbitrary cutoff.

- **Show in the sidebar** — listed in the saved group, as now.
- **Only show on search** — hidden from the list, but otherwise unchanged.

This hides nothing: **every context is always reachable by typing its name**,
whether or not it is listed, and that is what makes the option safe to offer.
It belongs on the context's own editor page beside the ephemeral toggle, and it
applies to the rail as well as the expanded list — the rail has less room, not
more.

### 10. Context timers

A context is a unit of work, which makes it the natural thing to time. Each
context gets a timer toggle; with it on, the clock runs while the context is
focused and stops when it is not, so the total is time actually spent rather
than time the window was open.

- Per-context enable switch, off by default — not every context is work worth
  measuring.
- Time accrues only while the context is the active one, using the same
  focus tracking the sidebar already does.
- Today and total shown on the context's row and in its editor.
- History kept per day, so it can answer "how long did I spend on this".

Optional later: a target per context with a notification when it is reached, and
an idle threshold so time does not accrue while the machine is untouched.

### 16. Theming like the rest of the session — *done*

One built-in look, and `$XDG_CONFIG_HOME/context/style.css` loaded over it —
the same contract as waybar or swaync, so whatever generates the desktop's
colours can template Context's too. Colours are `@define-color ctx_*` names
that reach both the widgets and the Cairo drawing, the file is watched so
saving restyles the running launcher, and the scheme setting is gone: a look
is a file, not an option. Constraints recorded in CLAUDE.md under "Theming".

### 17. The overview — *in progress*

A new direction taking shape step by step, keeping the sidebar as it is. Done
so far: an Overview button across the top of the sidebar; an `overview` command —
one screen with the search bar on top, contexts (open, then saved) on one side
and the installed apps on the other, where clicking an app creates a context
around it and opens it on the spot; and parity between the two views, which
now share their rows (`rows.py`). Both search apps as well as contexts, both
start a context from an app or a typed name, and a context's row offers the
same handles wherever it is listed. Next steps to be decided as it gets used.

### 18. A context's own menu — *done*

Right-clicking a context row opens its menu: open, open an app inside it, edit,
close, forget. Forgetting asks in the menu itself, the way the editor asks in
its row — a menu is deliberate, but one click either side of "Close" must not
lose a context.

"Open app here" is §6's remaining half done from the other direction: the app
grid, asked which application to add to an existing context rather than which
to build a new one around. The app joins the context's definition and the
context is relaunched, so only the missing window opens.

### 21. Saving from the list, and the no-context — *done*

A context that has drifted grows a save button where it is listed, in both the
sidebar and the overview: the button's presence is the prompt, and it goes
again once there is nothing to keep. Everything running that belongs to no
context is listed as a context of its own — "No context" — which can be closed
like any other, or saved, which gathers those windows into a workspace of their
own, captures them from there and opens the editor to name it.

Both need to know what is live, so the poll reads open, focused, drifted and
homeless in one pass (`launcher.read_live_state`) rather than a query per
question on the GTK main loop.

### 22. Sidebar contents, and pinning — *done*

Settings say which parts of the sidebar are shown: search, the overview button,
saved contexts, apps. And when the sidebar expands itself on hover, the header
button pins rather than collapses — it was the only control that did the
opposite of what it looked like, closing the sidebar under the pointer that was
holding it open.

### 23. Finding an app in the overview — *done*

The grid filters by the freedesktop main categories, offering only the ones
something is actually filed under, and groups itself three ways: by recency
(headed "just now", "3 hours ago", "2 days ago" — `uistate` records when each
application was launched), A–Z under letter headings, and In contexts, which
splits what you work in from everything else. Clicking an app either starts a
context around it or adds it to the one you are in, whichever the toggle above
the grid says.

### 20. Choosing on an overlay — *done*

The editor's layout dropdowns never kept what was picked in them, because a
popover on a layer-shell overlay drops the click. Presets are now thumbnails
of the arrangement they apply and the screen mode is a segmented control, both
plain buttons, and only the arrangements that hold exactly as many windows as
the screen has are offered — padding "Three columns" out to two windows meant
two thirds of a screen and a gap. The adopt window's per-window dropdown went
the same way. The
underlying popover behaviour is still unexplained — see the gotcha in
CLAUDE.md — so anything that must choose on an overlay avoids popovers.

### 19. Notifications — *done*

What the launcher reports goes to the desktop's notification daemon rather
than to a toast over its own list, which nobody sees while it is a rail. The
drift prompt's "Save layout" is a notification button, so the machinery has to
carry actions, not only text. A `notifications` setting turns them off.

### 24. The scratchpad — *done, then cut back*

Somewhere to type, in the sidebar itself. One global scratchpad and one per
context; no names, no list, nothing to create or delete, saved as you type. The
button opens the same text full-screen, which is the only difference between the
two views.

It shipped larger than this and was cut back the same day: named notes, a list
to pick from, and an append-only version history where editing an old version
appended rather than truncating. The history worked and was pinned by tests, and
none of it earned its place — a scratchpad is competing with the back of an
envelope, and anything you have to open, name or file has already lost. Removed
outright rather than hidden behind a setting, and the old file format is not
read: a hard cutover.

What survived is the part that was never about scale: the line markers. Plain
text with `- `, `- [ ] ` and two spaces per level, so the file stays readable
and the checklist view is a rendering rather than a second document.

What the cut revealed: the scratchpad had been living inside the sidebar's
list/empty stack, so it vanished exactly when there were no contexts — which is
when somewhere to jot something is worth most. It sits below the stack now and
is not part of what decides between the two.

Left for later: nothing prunes a context's scratchpad when the context is
forgotten (`NoteStore.forget` exists and nothing calls it), and there is no
search across scratchpads.

### 25. Packaged, with modules that merge — *done*

A flake package, an overlay, a NixOS module and a home-manager module, over a
settings loader that reads a chain of files rather than one.

The chain is what makes the modules usable. Before it, the only way to declare
settings was to own `settings.json` — the file Context writes — so home-manager
had to `force` it and every change made on the settings screen was reverted at
the next rebuild. Declared and imperative could not both exist.

Now: `/etc/xdg` drop-ins, then home drop-ins, then the file Context writes, each
merged over the last. Two things had to be true together, and either alone is
useless:

1. **Context writes only what was changed.** A snapshot of every setting names
   every key, and a layer that names every key overrides everything below it —
   one visit to the settings screen would detach the machine from its
   declaration for good.
2. **A module writes only what was set.** Every option is `nullOr` with a `null`
   default and nulls are dropped. With ordinary Nix defaults the module emits a
   complete file, and merely *enabling* the home-manager module would undo a
   NixOS declaration it was never told about.

Caught while wiring it up: the loader scanned `settings.d` in the home directory
but only `settings.json` under `/etc/xdg`, so the NixOS module's file was
written and never read. Both directories get both now, and the declared-contexts
loader is symmetric with it.

Left for later: no lock mechanism, so an administrator cannot pin a setting the
user may not change — dconf has one and it is the obvious next thing anyone
deploying this to more than one machine will want. `nix flake check` covers
x86_64 only.

### 26. `test_editor.py` is not isolated from itself

`test_the_preview_edit_hotspot_opens_the_resource_page` fails when the rest of
`test_editor.py` has run before it, but only in the Nix build sandbox: it passes
on a real display, under a local `xvfb-run`, and on its own in the sandbox. The
error is `New application windows must be added after the
GApplication::startup signal has been emitted`, so an earlier test is leaving an
application alive that the next one's window attaches to.

Verified pre-existing on an unmodified checkout, so it is not about packaging.
It is deselected in `checks.tests` and nowhere else — the local suite still runs
it. Worth fixing properly: the fixture almost certainly needs to wait for the
application to finish shutting down rather than trusting `app.quit()` to have
completed by the time the next test builds a window.

## Next

### 11. Ephemeral teardown

`ephemeral` is stored and editable but nothing acts on it. Closing an ephemeral
context should remove its workspace, delete its browser profile, and drop it from
the list — carefully, since "throw this away" must never eat real work. Needs a
confirmation path and a definition of what counts as unsaved.

### 12. Session restore

`xdg-session-management-v1` was merged 2026-03-23 after six years, and KWin has a
draft implementation. This is the protocol that finally makes window position and
state restoration possible on Wayland. Once Hyprland supports it, a context can
restore its actual layout rather than re-deriving it from a recipe. Worth tracking;
not actionable yet.

### 13. Login flow

Show the launcher at login: previous contexts, or a new-context page.

One design caution: the current concept assumes every session begins with an
intentional context choice. A lot of real computer use is "unlock and glance at one
thing," and forcing a decision there is exactly the friction that made Activities
feel heavy. Needs a zero-friction default path — resume last context, or a plain
desktop — before this becomes the login entrypoint.

### 14. Hyprland as the primary target — *done*

Hyprland is the only backend. Cinnamon is gone: it could not preselect a split,
could not resize a tiled window, and could only ever remove its *last* workspace,
so closing a context from the middle renumbered every handle after it. Keeping it
meant every feature had to be expressible in the weaker of the two.

Anywhere Hyprland is not running now falls back to `NullBackend`, where apps
launch onto the current workspace and contexts are names without containers.
Development still needs a real session on a spare VT or VM, since nesting is
impossible (see README).

## Later

- **Structured logging beyond the basics.** `logging_setup` gives levelled logging
  to `$XDG_STATE_HOME/context/context.log`, controlled by `CONTEXT_LOG_LEVEL`, with
  rotation. Still worth adding: a `--log-level` flag, and coverage of the launcher
  and adapters, which currently log only through the app.
- **Capture current state into a context** — "save what I have open." Needs live
  tab/window enumeration, so this is where a Firefox extension earns its cost.
- **Per-context identity** — different logins per context, mostly free from the
  profile-per-context design.
- **Context templates** — parameterised recipes ("code project" given a repo path).
- **Nested or related contexts** — only if a real need appears; this is where scope
  creep killed Activities.

## Non-goals

- **Building a compositor.** Multi-year work (input, HiDPI, multi-monitor, fractional
  scaling, XWayland, screen sharing, color management, accessibility), none of which
  is this idea. Build on Hyprland.
- **Being a full desktop environment.** This is a shell for contexts, not a
  replacement for panels, settings, and notifications.
- **A third organizing axis.** No contexts *and* workspaces *and* window groups. One
  concept. This is the specific mistake that sank Plasma Activities.

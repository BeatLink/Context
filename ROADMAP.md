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

### 4. Separate open contexts from saved ones — *next*

The list mixes two different things: contexts that are running right now, and
definitions sitting on disk. They want different actions.

- **Open contexts** — shown as a distinct group, with a close button. Closing is
  the common action; it is not something you do to a definition.
- **Saved contexts** — no destructive action in the list. Deleting moves into the
  editor, so forgetting a context is a deliberate act on its settings page rather
  than a stray click next to launch.

The state is already known: `context_is_open()` answers it per context, and the
row already renders an `open` marker. What is missing is the grouping and moving
delete out of the row.

### 5. Collapse the sidebar to a rail

The sidebar reserves 380px permanently, which is a lot to give up for something
mostly idle. A toggle should shrink it to taskbar width — an icon per context,
no titles or search — and expand it back.

Collapsed it works like the bar's window list: one icon per context, open ones
marked, click to switch. Expanded it is the current launcher.

The width is already configurable (`CONTEXT_SIDEBAR_WIDTH`) and the exclusive zone
follows the window size, so the compositor side needs no new work: the toggle
changes the size request and swaps the content. Worth persisting the state so it
survives a restart.

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

### 7. Switch contexts and windows

Two related pickers, both of which the desktop currently answers separately:

- **Context switching** — jump to a context by name. The sidebar list does this
  by clicking, but there is no keyboard path, and no equivalent of alt-tab
  between the last two.
- **Window switching** — every window in the current context, and optionally
  across all of them. `hyprctl clients` already reports enough to build this;
  the bar's taskbar covers the current workspace only.

Together these replace the bar's window list and rofi's window mode.

### 8. Move windows between contexts

A window opened in the wrong place should be movable rather than closed and
relaunched. `hyprctl dispatch movetoworkspacesilent <ws>,address:0x…` does the
work; what is missing is the UI — a menu on the window, or dragging its tile
between contexts in the sidebar.

This also covers adopting a window that Context never launched, which is the
other half of tracking windows rather than profiles (item 3).

### 8b. Adopt every window into a context — *reconnect done*

The goal is that no window is homeless: everything on screen belongs to some
context, so there is never a pile of windows the shell knows nothing about.

**Reconnecting is done.** Context can be restarted — a crash, an update, a
manual relaunch — while the windows it opened carry on. On startup it re-adopts
contexts whose workspaces still hold windows, and drops handles for those that
do not, so a restart neither offers to relaunch what is running nor reuses an
empty workspace.

Still to do:

- **Adopt unmanaged windows.** Windows on a workspace no context owns —
  everything opened before Context started, or launched from a keybind — should
  be offerable to a context, either an existing one or a new one made from them.
- **Adopt a whole workspace.** "Turn what is on this workspace into a context",
  which is how an existing session becomes a saved context without recreating it
  by hand. This is also the natural way to bootstrap: the first context is
  usually one you are already sitting in.
- **A home for the rest.** Whatever is not adopted needs somewhere to be, even if
  that is a plain "Unsorted" context, so the invariant actually holds.

### 8c. Save what a context becomes

A context is a recipe that is instantiated and then diverges: windows get
resized, a tab is opened, an app is added. None of that is written back, so
reopening rebuilds the original recipe and the arrangement settled into is lost.

What is worth capturing, roughly in order of value:

- **Layout.** Window sizes are already readable from `hyprctl clients`, and the
  reverse of `apply_ratios` is measuring the same spans. Dragging a divider
  should update the context's slots.
- **Membership.** An app launched into a context by hand should be offerable as
  part of it, which is the same mechanism as adopting unmanaged windows (8b).
- **Resources.** Firefox already restores its own tabs from its profile, so
  nothing is needed there; a VS Code path that the user changed is worth writing
  back.

Open question: **when**. Saving continuously makes a context drift into whatever
it was last left as, which is not always wanted — sometimes the recipe is the
point and today's mess is not. The likely answer is explicit ("Save arrangement")
with an opt-in per context, rather than always-on, but this needs using before
deciding.

## Phasing out what Context replaces

The Hyprland config mirrors Cinnamon feature by feature. As Context takes over a
job, the config-level version should go rather than compete with it — two things
managing windows is how the minimise black hole happened, where a special
workspace silently swallowed every newly launched window.

| Hyprland feature | Replaced by | Status |
| --- | --- | --- |
| `$mod, M` minimise to a special workspace | contexts hold windows | **removed** |
| Workspace-wide float toggle | layouts place windows | **removed** |
| rofi `drun` on `$mod+Space` | launching into a context (item 6) | after item 6 |
| rofi `window` on `$mod+W` | window switcher (item 7) | after item 7 |
| `wlr/taskbar` in the bar | context and window switching (item 7) | after item 7 |
| `hyprland/workspaces` in the bar | the sidebar's context list | after item 5 |
| `$mod, 1..5` workspace binds | context switching (item 7) | keep as an escape hatch |
| Overview | contexts are the overview | reconsider after item 7 |

Numbered workspaces are worth keeping even once contexts cover the same ground:
they are the way back to a plain desktop when Context is not running, which
matters while it is still the thing under development.

### 9. Settings

There is no settings page: everything is an environment variable
(`CONTEXT_SIDEBAR_EDGE`, `CONTEXT_SIDEBAR_WIDTH`, `CONTEXT_BACKEND`,
`CONTEXT_LOG_LEVEL`), which is fine for development and wrong for a shell people
use. A settings view should cover at least the sidebar's edge and width, whether
it starts collapsed, and the log level.

Per-context, the one that matters for the sidebar staying readable:

- **Show in the sidebar** — pinned contexts are always listed.
- **Only show on search** — the context still exists and still opens by name, but
  does not take up room in the list.

With a handful of contexts everything can be listed; with fifty it cannot, and
the alternative to this option is an arbitrary cutoff. It lives on the context's
own editor page alongside the ephemeral toggle.

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

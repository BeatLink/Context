# Context

A context-oriented desktop shell.

Rather than managing windows and workspaces, you work in **contexts**: named groups
of applications opened to specific resources, each focused on doing one thing.
"Surf reddit" is a Firefox window on reddit. "Work on coding project" is VS Code on
one side and API docs on the other. "Pay bills" is a banking tab and a spreadsheet.

You don't arrange windows. You say what you're doing, and the context is what
appears.

## Why

The dominant desktop metaphor is application- and document-centric: you manage
windows, and the *task* they serve exists only in your head. Contexts make the task
the object you manipulate.

This idea has a research lineage — **activity-based computing** (Giornata, Kimura,
Bardram et al.) — where "each activity groups multiple application windows with
associated resources." It has been validated repeatedly in studies and has
repeatedly failed to reach mainstream. The closest shipped analogue, KDE Plasma
Activities, was proposed for outright removal in Plasma 6; its own maintainers cited
"conceptually unclear" scope, frequent bugs, and low adoption *even in KDE's own
apps*.

Two lessons shape this project:

1. **Don't overlap with workspaces.** Activities failed partly by being a third
   axis next to virtual desktops and window groups. Here, a context *is* the
   workspace — one concept, not a layer on top of one.
2. **App integration is the whole problem.** "Firefox at reddit" and "VS Code at
   this workspace" are per-app work. That's not a detail to add later; it's the
   feature. See [ROADMAP.md](ROADMAP.md).

## Running

```sh
nix develop
python3 -m context
```

Contexts persist to `$XDG_DATA_HOME/context/contexts.json`.

## The launcher

The entrypoint you see when you log in.

- **Text bar** — doubles as create and search. Type a name and press Enter to start
  a new context; typing filters the list as you go. An exact title match (case
  insensitive) opens the existing context rather than duplicating it, and the button
  flips `Start` → `Open` so you can see which will happen.
- **Context list** — previous contexts, most recently used first. Click a row to
  launch it, the pencil to edit it, the × to forget it. Open contexts are marked
  `open` and gain a stop button that closes them.
- <kbd>Down</kbd> moves from the bar into the list. <kbd>Esc</kbd> goes back a page,
  or closes the launcher from the top level.

Creating a context opens the **app selector**: a searchable list of installed
applications from their desktop entries, with icons and checkboxes. The **edit
page** is the same selector plus the context's title and an ephemeral toggle.

Apps that can open *at* something show a pencil button once selected, leading to a
page where you say what. For Firefox that's a list of URLs, one per line; the row
subtitle then summarises them (`reddit.com +2 more`).

## Resources and adapters

A context holds **resources**: an app plus what it should open.

```json
{"app_id": "firefox.desktop", "urls": ["https://reddit.com"]}
```

An **adapter** turns a resource into a launch. `adapter_for()` picks the first one
that claims it, falling back to a generic desktop-entry launch, so apps with no
special handling still work and adding an adapter is always additive.

### Firefox

One **profile per context**, under `$XDG_DATA_HOME/context/firefox-profiles/<id>`.

Verified on Firefox 153: a single invocation with several URLs opens one window
with one tab each. There are no window-targeting flags, so a shared profile would
send new tabs to whatever window was last focused — contexts would interleave and
could not reliably reuse their own window. A separate profile also buys tab restore
on reopen, an isolated cookie jar, and a distinct PID for window matching.

First launch seeds the configured URLs. Later launches pass none, so Firefox's own
session restore reopens what you actually left there rather than resetting to the
original list. New profiles get a `user.js` that suppresses onboarding.

Costs: ~150–250MB per live instance, and no shared history or bookmarks.

## Contexts and workspaces

A context lives in a workspace. Opening one:

- **Workspace exists and holds windows** → switch to it, launch nothing.
- **Otherwise** → create it if needed, switch to it, launch the apps. An existing
  but *empty* workspace still relaunches, which is what makes a closed context
  reopen properly.

**Closing** a context is distinct from forgetting it: its windows are asked to
close, and the definition, URLs, and history stay. Reopening rebuilds it.

Closing only ever touches windows on that context's own workspace — never sticky
windows, never another workspace. Whether the empty workspace is then removed is
per-backend: Hyprland's named workspaces disappear by themselves, while Cinnamon
can only drop the *last* workspace, since `num-workspaces` is a count and removing
one from the middle would renumber the rest and repoint other contexts' handles.
When it can't be removed the workspace is simply left in place.

Which window manager provides the workspace is behind a backend interface, so the
app stays testable on whatever session is actually running.

| Backend | Handle | Requires |
| --- | --- | --- |
| `hyprland` | named workspace, `ctx-<slug>` | `hyprctl` + `HYPRLAND_INSTANCE_SIGNATURE` |
| `cinnamon` | workspace index | `wmctrl` + `org.cinnamon.desktop.wm.preferences` |
| `none` | — | nothing; apps open on the current workspace |

`detect()` prefers Hyprland, falls back to Cinnamon, then `none`. Override with
`CONTEXT_BACKEND=hyprland|cinnamon|none` — also how tests force the no-op backend.

### Workspace identity

Each backend returns an opaque **handle**, stored per-backend on the context as
`workspaces: {backend: handle}`. Identity comes from the handle, never the title, so
renaming a context relabels its existing workspace instead of orphaning it and
creating a duplicate. The map is per-backend, so one context can hold both a
Cinnamon index and a Hyprland name without collision.

### Testing the Hyprland backend

Hyprland **cannot be nested inside an X11 session**. Since 0.42 it uses its own
aquamarine backend, which offers only DRM and Wayland — no X11 backend exists, and
its Wayland client demands protocol versions newer than Weston, Sway, or Muffin
advertise. Verified on this machine:

| Host | Failure |
| --- | --- |
| Cinnamon X11 (direct) | `DRM backend failed` — GPUs held by the X server |
| Weston 15 | `wl_compositor: expected at most 5, got 6` |
| Sway 1.12 | `xdg_wm_base: expected at most 5, got 6` |
| Cinnamon on Wayland (Muffin 6.6.3) | `wl_compositor: expected at most 5, got 6` |

What works: Hyprland inside Hyprland, a spare VT (`chvt 2`), or a VM. The Cinnamon
backend exists so everything else stays developable meanwhile.

## Layout

| Path | Purpose |
| --- | --- |
| `context/app.py` | `Adw.Application` subclass and `main()` |
| `context/window.py` | Launcher window, entry bar, context rows, navigation |
| `context/app_picker.py` | App selector, and the edit page |
| `context/apps.py` | Installed-app discovery via `Gio.AppInfo` |
| `context/resources.py` | `Resource`, URL parsing, legacy `apps` migration |
| `context/resource_page.py` | The "what should this open?" page |
| `context/adapters/base.py` | The `Adapter` protocol and `GenericAdapter` |
| `context/adapters/__init__.py` | `adapter_for()` and the adapter registry |
| `context/adapters/firefox.py` | Profile-per-context Firefox launching |
| `context/store.py` | `Context` dataclass and JSON-backed `ContextStore` |
| `context/launcher.py` | Instantiating a context: workspace + app launch |
| `context/backends/base.py` | The `Backend` protocol, `Workspace`, `NullBackend` |
| `context/backends/__init__.py` | `detect()` and the backend registry |
| `context/backends/hyprland.py` | Named workspaces over `hyprctl` |
| `context/backends/cinnamon.py` | Indexed workspaces over `wmctrl` + gsettings |

## Status

Contexts can be created, edited, listed, and launched: apps start from their desktop
entries into a per-context workspace, and reopening switches rather than relaunching.

Firefox opens at a context's URLs in its own profile. Still to come: adapters for
VS Code and terminals, window placement within a context, and teardown for
ephemeral contexts (`FirefoxAdapter.teardown` exists but nothing calls it yet).

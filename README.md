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
  launch it, the pencil to edit it, the × to forget it.
- <kbd>Down</kbd> moves from the bar into the list. <kbd>Esc</kbd> goes back a page,
  or closes the launcher from the top level.

Creating a context opens the **app selector**: a searchable list of installed
applications from their desktop entries, with icons and checkboxes. The **edit
page** is the same selector plus the context's title and an ephemeral toggle.

## Contexts and workspaces

A context lives in a workspace. Opening one:

- **Workspace already exists** → switch to it, launch nothing. The apps are
  already there.
- **Workspace doesn't exist** → create it, switch to it, launch the apps.

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
| `context/store.py` | `Context` dataclass and JSON-backed `ContextStore` |
| `context/launcher.py` | Instantiating a context: workspace + app launch |
| `context/backends/base.py` | The `Backend` protocol, `Workspace`, `NullBackend` |
| `context/backends/__init__.py` | `detect()` and the backend registry |
| `context/backends/hyprland.py` | Named workspaces over `hyprctl` |
| `context/backends/cinnamon.py` | Indexed workspaces over `wmctrl` + gsettings |

## Status

Contexts can be created, edited, listed, and launched: apps start from their desktop
entries into a per-context workspace, and reopening switches rather than relaunching.

Not yet built: opening apps *to specific resources* (the actual point — see
[ROADMAP.md](ROADMAP.md)), window placement within a context, and teardown for
ephemeral contexts.

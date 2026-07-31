# Context

A context-oriented desktop shell.

Rather than managing windows and workspaces, you work in **contexts**: named
groups of applications opened to specific things, each focused on doing one job.
"Surf reddit" is a Firefox window on reddit. "Work on a project" is your editor
on one side and API docs on the other. "Pay bills" is a banking tab and a
spreadsheet.

You don't arrange windows. You say what you're doing, and the context appears.

## Requirements

- A Wayland compositor with `zwlr-layer-shell`. Hyprland is the target.
- Nix, for the development shell.

## Running

```sh
nix develop
python3 -m context
```

Context is single-instance: running it again raises the launcher you already
have.

## The launcher

A sidebar docked to the edge of the screen, with space reserved so windows sit
beside it rather than underneath.

- **Search bar** — type to filter, or type a new name and press <kbd>Enter</kbd>
  to create a context. A name matching an existing context opens it instead.
- **Open** — contexts running right now. The one you are in is highlighted.
  Click a row to switch to it, the stop button to close it, the pencil to edit.
- **Saved** — everything else, in a section below. It is expanded when nothing
  is open and folds away once something is, one click from opening again.

<kbd>Down</kbd> moves from the search bar into the list. <kbd>Esc</kbd> clears
the search.

**Collapsing.** The button in the top corner shrinks the sidebar to a rail: one
icon per context, open ones highlighted, click to switch. The space it was
reserving goes back to your windows. The arrow at the top expands it again, and
it reopens the way you left it.


## The editor

Creating or editing a context opens a full-screen editor: the window arrangement
on top, the app catalogue below.

**Layout.** A scale model of your monitor, one rectangle per window. Drag a
window to move it, its corner to resize, the × to remove it. Each window shows
the app's icon, with its name on hover, and a pencil for what it opens.
Everything snaps to a 5% grid, and a dropdown offers arrangements to start from:
maximised, side by side, top and bottom, main and side, three columns, main and
stack, grid.

**Apps.** Every installed application, searchable. Click a card to add it to the
layout; add one twice to give it two windows.

**Details.** The context's name, an ephemeral toggle, and a Forget button.

Layouts are stored as fractions of the monitor, so they carry between displays.

## What apps open

Each app in a context can be pointed at something.

| App | Opens |
| --- | --- |
| Firefox | A list of URLs, one window with a tab each |
| VS Code / VSCodium | A folder, a file, or a `.code-workspace` |
| Terminals | A directory, optionally running a command |
| Anything else | Just launches |

**Firefox** opens in the profile you already browse with, so your addons,
logins and history are there. Turning on **Give this context its own profile**
keeps its tabs, cookies and history separate instead, and restores them when
you come back — at the cost of not carrying your addons and logins over.

**Compatibility.** Two switches per app, for when it does not behave:

- **Open a new window** — turn off if the app should reuse a window it has.
- **Single instance only** — for apps that refuse to run twice.

## Opening and closing

Opening a context switches to its workspace. If it is not running, its apps
launch and tile into the layout; if it is, you simply arrive where you left off.

**Closing** a context shuts its windows but keeps the context itself, so opening
it again rebuilds it. **Forgetting** a context deletes the definition and lives
in the editor, behind a confirmation.

If Context is restarted while contexts are open, it reconnects to them.

## Settings

The gear in the launcher's header. Changes apply as you make them; the few that
need a restart say so.

| Setting | Meaning |
| --- | --- |
| Colour scheme | Light, dark, or whatever the desktop is set to |
| Edge | Which side the launcher docks to |
| Expanded width | Pixels reserved when the launcher is open |
| Collapsed width | Pixels reserved by the rail |
| Expand on hover | Open the launcher while the pointer is over the rail |
| Hover delay | How long to wait first, so passing over does not open it |
| Window manager | Which backend drives workspaces |
| Refresh interval | How often the open list is re-checked |
| Log level | How much detail is written to the log |

Settings are stored in `$XDG_CONFIG_HOME/context/settings.json` and can be
edited by hand. Environment variables override them for a single run:

| Variable | Meaning |
| --- | --- |
| `CONTEXT_SIDEBAR_EDGE` | `left`, `right`, `top`, or `bottom` |
| `CONTEXT_SIDEBAR_WIDTH` | Expanded thickness in px |
| `CONTEXT_RAIL_WIDTH` | Collapsed thickness in px |
| `CONTEXT_BACKEND` | `hyprland` or `none` |
| `CONTEXT_LOG_LEVEL` | `debug`, `info`, `warning`, `error` or `critical` |

## Theming

Colours come from `$XDG_CONFIG_HOME/context/theme.json`. Anything it does not
set keeps the default:

```json
{
  "accent": "#5ac0c0",
  "surface": "#1e1e1e",
  "slot_fill": "#5ac0c052",
  "tile_selected": "#5ac0c038"
}
```

Light and dark have separate palettes, so a colour that works on one does not
disappear on the other. Anything you set explicitly is used in both. **Write the
default theme** in settings creates the file for you. Set `CONTEXT_THEME` to
load one from elsewhere.

## Where things are kept

| Path | Contents |
| --- | --- |
| `$XDG_DATA_HOME/context/contexts.json` | Context definitions |
| `$XDG_DATA_HOME/context/firefox-profiles/` | Per-context browser profiles |
| `$XDG_STATE_HOME/context/context.log` | Log, rotated |
| `$XDG_STATE_HOME/context/ui.json` | Whether the sidebar is collapsed |
| `$XDG_CONFIG_HOME/context/settings.json` | Settings |
| `$XDG_CONFIG_HOME/context/theme.json` | Theme |

## Window managers

Context drives the compositor through a backend, chosen automatically:

| Backend | Requires |
| --- | --- |
| `hyprland` | `hyprctl` and a running Hyprland |
| `none` | Nothing; apps open on the current workspace |

`CONTEXT_BACKEND` overrides the choice.

Under `none` a context is still a named group of apps opened to specific
things — it just has no workspace of its own, so everything opens where you
already are and closing a context is unavailable.

Hyprland cannot run nested inside another session, so testing the Hyprland
backend needs a real session — a spare VT or a VM.

## Development

```sh
nix develop
python3 -m pytest tests/ -q            # logic
xvfb-run -a python3 -m pytest tests/   # including the interface
```

See [CLAUDE.md](CLAUDE.md) for the working notes, [ROADMAP.md](ROADMAP.md) for
what is planned, and [FEATURES.md](FEATURES.md) for what an application needs to
do to work well inside a context.

## Layout

| Path | Purpose |
| --- | --- |
| `context/app.py` | Application entry point |
| `context/window.py` | The launcher sidebar |
| `context/editor.py` | The editor: layout preview and app grid |
| `context/editor_window.py` | Hosts the editor full screen |
| `context/resource_page.py` | What an app opens |
| `context/apps.py` | Installed-app discovery |
| `context/store.py` | Contexts, saved to disk |
| `context/layout.py` | Slots, presets, and layout repair |
| `context/launcher.py` | Opening and closing contexts |
| `context/theme.py` | Colours |
| `context/logging_setup.py` | Logging |
| `context/sidebar.py` | Docking the launcher to a screen edge |
| `context/settings.py` | User settings |
| `context/settings_page.py` | The settings page |
| `context/uistate.py` | Interface state that survives a restart |
| `context/adapters/` | How each app opens what it is given |
| `context/backends/` | How each window manager is driven |

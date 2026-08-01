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
the search and hands the keyboard back.

The launcher takes the keyboard when you click into it and gives it up when you
click away, like any other window. Opening a context hands focus to the windows
it opens.

**Collapsing.** The button in the top corner shrinks the sidebar, in whichever
way the collapse mode says:

- **A rail of icons** — one icon per context, open ones highlighted, click to
  switch. Keeps reserving the collapsed width.
- **Hidden entirely** — gives back all the space and leaves a sliver at the
  screen edge to hover over.
- **Never collapse** — removes the button; the launcher stays open.

A rail and never-collapse both stay pinned to the edge, reserving space; only
hiding gives it back. Either collapsing mode reopens the way you left it, and
the saved group stays folded or unfolded as you had it.


## Switching

| Command | Does |
| --- | --- |
| `switch` | Pick a context by name |
| `switch-window` | Pick a window in the context you are in |
| `switch-window-all` | Pick a window across every context |
| `previous` | Return to the last context |
| `settings` | Open the settings page |
| `overview` | Contexts and apps on one screen; an app opens as a new context |
| `toggle-rail` | Collapse or expand the sidebar |
| `restart` | Restart Context, keeping open contexts open |
| `move-window` | Send the focused window to another context |
| `adopt` | Give windows that belong to no context a home |
| `capture` | Save what the current context has become |
| `window-left` / `window-right` | Throw the window to the context's next screen |
| `fullscreen` `maximise` `float` `tile` `center` | Change the focused window |
| `group` / `ungroup` | Fold the window into a tabbed group, or take it out |

Run them as `python3 -m context <command>`. Context is single-instance, so each
one is handed to the running copy rather than starting another — which makes
them cheap to bind to a key.

Windows are listed most recently focused first, each labelled with the context
it belongs to. The window picker starts scoped to the context you are in and
switches to all of them from its header.

## The editor

Creating or editing a context opens a full-screen editor: the window arrangement
on top, the app catalogue below.

**Layout.** A scale model of your monitor, one rectangle per window. Drag a
window to move it, its corner to resize, the × to remove it. Each window shows
the app's icon, with its name on hover, and a pencil for what it opens.
Everything snaps to a 5% grid, and a row of thumbnails offers the arrangements
to start from — maximised, side by side, top and bottom, main and side, three
columns, main and stack, grid. Only the ones that hold exactly as many windows
as the screen has are shown, so the row changes as apps are added; past what
they cover, a generated grid is offered instead. The one your layout currently
matches is ringed, and none is once you have dragged a window off it.

**Apps.** Every installed application, searchable. Click a card to add it to the
layout; add one twice to give it two windows.

**Details.** The context's name, an ephemeral toggle, an isolated toggle, and a
Forget button.

Layouts are stored as fractions of the monitor, so they carry between displays.
The preview is drawn at the shape of the screen the launcher is on.

## Multiple monitors

A context spreads across every screen you have. It keeps a separate arrangement
for each number of attached screens, so working undocked and working at a desk
with two monitors are different layouts and both are remembered — plug a
monitor in and the two-screen arrangement comes back, unplug it and the
one-screen version does.

Contexts refer to screens by number, never by monitor. **Screens** in settings
says which physical monitor is screen 1, screen 2 and so on, and **Screen
modes** says how many arrangements a context can hold. Moving a cable or
rearranging the desk is one change there rather than an edit to every context.

The editor shows one preview per screen, side by side, labelled with the
monitor each number currently means. Drag a window off the edge of one preview
to move it to the next — the screen it would land on lights up while you drag.
**Layout for** above chooses which arrangement you are editing, so the
two-screen layout can be set up while undocked.

Opening a context brings up all of its screens at once and leaves you on the
first. Closing shuts all of them.

The launcher itself docks to one screen, chosen in settings, or to **all
displays** — one launcher per monitor, since a docked panel belongs to a single
screen. Naming a monitor that is not currently connected is fine: it is used
when you plug it back in, and until then the launcher appears on whichever
screen has focus.

## Managing windows

A window opened inside a context belongs to it. These move windows around
without the context losing track of them.

**Send a window elsewhere.** `move-window` picks a context and moves the focused
window into it. The context has to be open — a window cannot move somewhere that
does not exist yet.

**Adopt loose windows.** `adopt` lists everything belonging to no context and
offers each one a home, so nothing stays orphaned.

**Save what a context became.** A context drifts as you use it: windows get
moved, resized, opened and closed. `capture` reads the live positions back into
the arrangement for however many screens you have now, so reopening rebuilds
what you actually had. What each app opens is kept.

Context can also offer this itself, at whichever moment suits — whenever a
context changes, when you switch away from it, or when you close it. **Ask to
save** in settings chooses; a context is only offered once per run, so one you
decline stops asking.

**Throw a window between screens.** `window-left` and `window-right` move the
focused window to the context's next screen, staying inside the context.

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

**Compatibility.** Three switches per app, for when it does not behave:

- **Open a new window** — turn off if the app should reuse a window it has.
- **Single instance only** — for apps that refuse to run twice.
- **Isolate in this context** — turn off for an app that shares a database with
  another context. Only applies to isolated contexts.

## Isolated contexts

A context can be marked **isolated** in its editor. Its apps then launch without
being able to see copies of themselves running elsewhere, so an app that would
normally raise its existing window opens a new one here instead.

Turn it on for a context whose apps you want to run twice — two terminals, two
editors, the same app in two contexts at once.

Leave it off, or turn off **Isolate in this context** for the app in question,
when an app keeps a database shared with another context. Two copies writing at
once can lose data, and isolation is what stops them noticing each other.

Isolation is about visibility between copies of an app, not security. An
isolated app has the same access to your files and network as any other.

## Opening and closing

Opening a context switches to its workspace. If it is not running, its apps
launch and tile into the layout; if it is, you simply arrive where you left off.

**Closing** a context shuts its windows but keeps the context itself, so opening
it again rebuilds it. **Forgetting** a context deletes the definition and lives
in the editor and in a context's own menu, behind a confirmation either way.

Right-clicking a context — in the sidebar or the overview — opens its menu:
open it, open an app inside it, edit it, close it, or forget it. "Open app
here" shows the app grid; what you pick joins that context and opens in it.

Searching the sidebar searches applications as well as contexts, and starting
an app from those results makes a context around it, the same as the overview's
grid does.

If Context is restarted while contexts are open, it reconnects to them.

## Settings

The gear in the launcher's header. Changes apply as you make them; the few that
need a restart say so.

| Setting | Meaning |
| --- | --- |
| Monitor | Which screen the launcher docks to, or all of them |
| Screen modes | How many screen counts a context can hold a layout for |
| Screen 1, 2, … | Which physical monitor each screen number means |
| Edge | Which side the launcher docks to |
| Width | Pixels the launcher reserves |
| Collapse mode | A rail of icons, hidden entirely, or never |
| Collapsed width | Pixels reserved by the rail |
| Expand on hover | Open the launcher while the pointer is over it |
| Hover delay | How long to wait first, so passing over does not open it |
| Collapse delay | How long it stays open after the pointer leaves its zone |
| Window manager | Which backend drives workspaces |
| Refresh interval | How often the open list is re-checked |
| Ask to save | When to offer to keep a context's changes |
| Notifications | Report launches, closes and drift to the desktop |
| Log level | How much detail is written to the log |

Settings that do nothing in the current collapse mode are not shown: the
collapsed width only appears for a rail, and the hover settings disappear when
collapsing is off.

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

Context is styled the way waybar or swaync are: it ships a stylesheet, and
`$XDG_CONFIG_HOME/context/style.css` loads over it. Redefine any of the
published colours, write ordinary CSS against the `ctx-*` style classes, or
both:

```css
@define-color ctx_accent #ff8800;
@define-color ctx_surface #101010;

.ctx-rail-button {
    border-radius: 0;
}
```

The colour names cover everything Context draws, including the parts it paints
itself — the app tiles and the layout preview follow the same definitions.
**Write the style file** in settings creates the file with every colour
spelled out. The file is watched, so saving it restyles the running launcher.
Set `CONTEXT_STYLE` to load a file from elsewhere.

Because it is one CSS file with `@define-color` names, whatever generates the
rest of the desktop's colours — matugen, pywal, a home-manager template — can
emit Context's theme the same way it emits waybar's.

## Where things are kept

| Path | Contents |
| --- | --- |
| `$XDG_DATA_HOME/context/contexts.json` | Context definitions |
| `$XDG_DATA_HOME/context/firefox-profiles/` | Per-context browser profiles |
| `$XDG_STATE_HOME/context/context.log` | Log, rotated |
| `$XDG_STATE_HOME/context/ui.json` | Whether the sidebar is collapsed |
| `$XDG_CONFIG_HOME/context/settings.json` | Settings |
| `$XDG_CONFIG_HOME/context/style.css` | Stylesheet loaded over the built-in one |

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
| `context/monitors.py` | Which output things happen on |
| `context/arrangement.py` | How a context spreads across screens |
| `context/switcher.py` | Context and window pickers |
| `context/settings.py` | User settings |
| `context/settings_page.py` | The settings page |
| `context/uistate.py` | Interface state that survives a restart |
| `context/adapters/` | How each app opens what it is given |
| `context/backends/` | How each window manager is driven |

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
- **Open** — the overview first, then the contexts running right now. The one
  you are in is highlighted. Click a row to switch to it, the stop button to
  close it, the pencil to edit. The overview has none of those: it is somewhere
  to go and nothing else.
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
| `overview` | Go to the overview, which has a screen of its own |
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

## The overview

Every application installed, on one screen: a search over them, filtered by kind
and grouped by how they are ordered. Contexts are not here — the sidebar lists
those, and it stands open beside this.

**It is a place, not a panel.** The overview has a workspace of its own, and
going to it is a switch like any other. It cannot be closed, so there is always
somewhere to come back to: closing your last context leaves you here rather than
looking at an empty desktop. It is listed with your open contexts, first and
always, and the sidebar sits beside it the way it sits beside anything else.

It wears no titlebar, its own or the compositor's. There is nothing to close and
nowhere for it to go.

**The sidebar stands open beside it**, and is where the contexts are. However
the launcher is set to collapse, it is expanded for as long as home is on
screen, with every part of it showing — the search box, the saved group, the
scratchpad — whatever the sidebar settings say for the narrow column it is
everywhere else. Collapsing is only offered outside home, and leaving goes back
to however you had it. The two together are the whole screen: what you could
run on one side, what you are already doing on the other.

<kbd>Esc</kbd> clears the search, and again goes back to the context you came
from. <kbd>Enter</kbd> opens the first application matched. Editing a context or
picking an app opens over the overview and leaves it where it was.

An application row offers both answers: open it in a context of its own, or in
the context you came from — named, since standing on the overview "here" would
mean the overview. Clicking the row takes the first, since it is the one that
works whether or not anything is open, and right-clicking says both in words.

The applications filter by kind and group four ways: **Recent** by how long ago
you last opened each (just now, 3 hours ago, 2 days ago…), **A–Z** under letter
headings, **By kind** under the categories the desktop entries claim, and **In
contexts**, which splits what you actually work in from everything else.

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

**Or put it back.** A drifted context's card offers both: keep the windows as
they are, or put them back the way the context was saved. Restoring reopens
whatever was closed and then moves everything to where it belongs.

It never closes anything. A window you opened since is your work, so a context
that has drifted by having *more* than it was saved with stays that way —
missing is recoverable, surplus is not.

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

A context that has **drifted** — its windows opened, moved or closed since it
was saved — grows a save button in the list. Pressing it keeps the windows as
they are; the button goes when there is nothing left to keep.

Everything running that belongs to no context is listed as **No context**,
alongside the open ones. Closing it closes those windows; saving it gathers
them into a workspace of their own, captures where they landed, and opens the
editor to name what they have become.

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

## The scratchpad

Somewhere to type, in the sidebar itself. Not a list of notes to open first —
click into it and write.

There is **one global scratchpad** and **one for each context**, and that is
all of them. They have no names and there is nothing to create or delete, so
where something went is answered by where you were when you wrote it.

With both switched on you get a switch above the box — **Global** on the left,
where it stays whichever context you are in, and the context's own beside it,
which is the one that opens. **Show both at once** in settings stacks them
instead, so neither needs reaching for.

It saves as you type, and says so: the box reads *Saving…* while you write and
*Saved* once it has been written. There is no save button and nothing to
confirm. **Writing area height** sets how tall the box is in the sidebar.

The button beside it opens the same scratchpad full-screen, which is the only
difference between the two — more room, plus the checklist view and the buttons
that change what a line is.

**Formatting** is plain text with markers, so a scratchpad is readable and
editable as a file:

| Typed | Is |
| --- | --- |
| `- milk` | A bullet |
| `- [ ] milk` | An unticked checkbox |
| `- [x] milk` | A ticked one |
| Two spaces of indent | One level of nesting |

<kbd>Enter</kbd> on a list item starts the next one with the same marker, and
again on an empty item ends the list. <kbd>Ctrl</kbd>+<kbd>Enter</kbd> ticks the
checkbox on the current line. In the full view, **Checklist** draws the same
text as widgets where a box is something you click.

## Settings

The cogwheel in the launcher's header, in five tabs — **Launcher**,
**Overview**, **Scratchpad**, **Contexts** and **System**. Changes apply as you
make them; the few that need a restart say so.

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
| Search | Show the sidebar's search box |
| New context row | Show the row that starts a context |
| Overview row | Show the overview in the sidebar's list of open contexts |
| Saved contexts | Show the saved group beneath the open one |
| Apps | Show matching applications under the search results |
| Order contexts by | Most recently used, when they were made, or name |
| Sort applications by | How the overview's application list is ordered when it opens |
| Scratchpad | Somewhere to type in the sidebar |
| Global scratchpad | The one that is there wherever you are |
| Context scratchpads | One for each context, shown while you are in it |
| Scratchpad in the sidebar | Show it in the narrow list |
| Show both at once | Stack the two rather than switching between them |
| Writing area height | How tall the box is, per scratchpad |
| Log level | How much detail is written to the log |

Settings that do nothing in the current collapse mode are not shown: the
collapsed width only appears for a rail, and the hover settings disappear when
collapsing is off.

### Where settings come from

Settings are read from several files, merged setting by setting, and the last
file to mention a setting decides it:

| Read | File | Owned by |
| --- | --- | --- |
| 1 | `/etc/xdg/context/settings.d/*.json` | A NixOS module |
| 2 | `/etc/xdg/context/settings.json` | Hand-edited system config |
| 3 | `$XDG_CONFIG_HOME/context/settings.d/*.json` | A home-manager module, or you |
| 4 | `$XDG_CONFIG_HOME/context/settings.json` | Context itself |

Every entry in `XDG_CONFIG_DIRS` is read, least important first; drop-ins within
a directory are read in name order, so `50-` beats `20-`.

The last file is the only one Context writes, and it records **only the settings
you have actually changed**. Everything you have not touched keeps following the
files above it, so a declared configuration and the settings screen compose
instead of overwriting each other. **Reset your changes** in the settings screen
empties that file, and every setting goes back to what was declared.

The settings screen lists the whole chain and says how many settings each file
decides, so "I changed this and it went back" always has an answer on screen.

Environment variables override for a single run:

| Variable | Meaning |
| --- | --- |
| `CONTEXT_SETTINGS` | One file, replacing the whole chain |
| `CONTEXT_SETTINGS_PATH` | The chain itself, `:`-separated, least important first |
| `CONTEXT_SIDEBAR_EDGE` | `left`, `right`, `top`, or `bottom` |
| `CONTEXT_SIDEBAR_WIDTH` | Expanded thickness in px |
| `CONTEXT_RAIL_WIDTH` | Collapsed thickness in px |
| `CONTEXT_BACKEND` | `hyprland` or `none` |
| `CONTEXT_LOG_LEVEL` | `debug`, `info`, `warning`, `error` or `critical` |

## NixOS

The flake provides a package, an overlay, a NixOS module and a home-manager
module.

```nix
{
  inputs.context.url = "github:BeatLink/Context";

  # NixOS
  imports = [ inputs.context.nixosModules.default ];
  programs.context = {
    enable = true;
    settings.collapse_mode = "hidden";
    contexts = [
      { title = "Work on Context"; apps = [ "codium.desktop" "firefox.desktop" ]; }
    ];
  };
}
```

`inputs.context.homeModules.default` takes the same options and writes into the
home directory instead.

Both write a **drop-in**, never `settings.json`, so neither fights Context for
that file and neither has to be forced. Only the settings you actually set are
written, which is what lets the two modules and the settings screen stack:

| Layer | `priority` default | Beaten by |
| --- | --- | --- |
| NixOS module | 20 | The home-manager module, and you |
| home-manager module | 50 | You |
| The settings screen | — | Nothing |

`priority` changes the filename and therefore the position in the load order.

Declared contexts stack the same way, except that they add rather than override:
two files declaring contexts means having both, and only a repeated id is
resolved by order. Each declaration is taken in once and is an ordinary context
from then on, so editing or forgetting one in the launcher sticks.

`nix run github:BeatLink/Context` runs it without installing. The package does
not depend on Hyprland: `hyprctl` belongs to the compositor already running, and
Context falls back to no window manager when it is absent.

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

## Declared contexts

Anything that can write a file can declare contexts — a NixOS module, a
dotfile repository, a script. Put them in
`$XDG_CONFIG_HOME/context/contexts.json`:

```json
{
  "contexts": [
    {
      "title": "Work on Context",
      "resources": [{"app_id": "codium.desktop"}, {"app_id": "firefox.desktop"}],
      "isolated": true
    }
  ]
}
```

They are seeds, not managed state: each is taken into the store once and is an
ordinary context from then on, so editing or forgetting one sticks rather than
being undone at the next start. A context with no layout gets the preset for
however many applications it holds.

Transparency is the alpha of `ctx_surface` — `rgba(30, 30, 30, 0.75)` or
`#1e1e1ebf`. The docked sidebar honours it; the editor, overview, settings and
pickers stay opaque, since a haze over the whole screen is not the same thing
as a translucent strip along an edge.

## Where things are kept

| Path | Contents |
| --- | --- |
| `$XDG_DATA_HOME/context/contexts.json` | Context definitions |
| `$XDG_DATA_HOME/context/scratchpad.json` | The scratchpads |
| `$XDG_DATA_HOME/context/firefox-profiles/` | Per-context browser profiles |
| `$XDG_STATE_HOME/context/context.log` | Log, rotated |
| `$XDG_STATE_HOME/context/ui.json` | Whether the sidebar is collapsed |
| `$XDG_CONFIG_HOME/context/settings.json` | The settings you have changed |
| `$XDG_CONFIG_HOME/context/settings.d/*.json` | Settings declared elsewhere |
| `$XDG_CONFIG_HOME/context/contexts.json` | Contexts declared elsewhere, taken in once |
| `$XDG_CONFIG_HOME/context/contexts.d/*.json` | The same, one file per declaring thing |
| `/etc/xdg/context/` | The system-wide copy of all four |
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

Three packages, split by what the code answers to:

| Path | Purpose |
| --- | --- |
| `context/app.py` | Application entry point and command routing |
| `context/state/` | What Context remembers |
| `context/state/store.py` | Contexts, saved to disk; declared contexts |
| `context/state/settings.py` | User settings |
| `context/state/uistate.py` | Interface state that survives a restart |
| `context/state/resources.py` | What a context holds |
| `context/state/layout.py` | Slots, presets, and layout repair |
| `context/state/arrangement.py` | How a context spreads across screens |
| `context/system/` | Dealings with the rest of the system |
| `context/system/launcher.py` | Opening, closing and switching contexts |
| `context/system/backends/` | How each window manager is driven |
| `context/system/adapters/` | How each app opens what it is given |
| `context/system/apps.py` | Installed-app discovery, grouping, search |
| `context/system/monitors.py` | Which output things happen on |
| `context/system/isolation.py` | Private session buses |
| `context/system/notify.py` | The notification daemon |
| `context/system/logging_setup.py` | Logging |
| `context/ui/` | Everything drawn |
| `context/ui/window.py` | The launcher sidebar |
| `context/ui/rail.py` | The collapsed sidebar |
| `context/ui/rows.py` | The rows and tiles both views share |
| `context/ui/overview.py` | Everything on one screen |
| `context/ui/editor.py` | The editor: layout preview and app grid |
| `context/ui/switcher.py` | Context and window pickers |
| `context/ui/settings_page.py` | The settings screen's page |
| `context/ui/sidebar.py` | Layer-shell: docking, overlays, keyboard |
| `context/ui/theme.py` | Colours and the style.css contract |
| `context/ui/widgets.py` | Plain-GTK stand-ins for libadwaita |

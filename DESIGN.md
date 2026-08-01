# Design

Where a thing goes, and why. **Proposed, not settled** — this is written to be
argued with, and nothing here has been built yet.

The other three files answer different questions. [README.md](README.md) says
what the software does. [CLAUDE.md](CLAUDE.md) records constraints found the
hard way, so they are not rediscovered. [ROADMAP.md](ROADMAP.md) says what is
planned. This one says **how to decide where something belongs**, so the answer
stops being reached at one feature at a time.

## The problem this is fixing

Context has eight surfaces and twenty-eight settings. The overlap that keeps
turning up — contexts listed in two places, three separate application grids,
the same app opened three different ways — was not carelessness. It is a
pattern: every time "where does this go?" came up, the answer was *here too,
and a setting to hide it*.

Five settings are the evidence. `show_search`, `show_new_context`, `show_saved`,
`show_apps` and `show_notes` are not preferences. They are unresolved design
decisions turned into configuration, and each one is a question that was asked
and not answered.

The premise already contains the answer. Context's claim is that you work in
contexts rather than in windows and workspaces, which means **a context is the
only noun anyone should have to hold**.

## The rule

**Every surface must be explainable as "this is where a noun lives."** One home
each:

| Noun | Home |
| --- | --- |
| Contexts | the sidebar |
| Applications | the overview |
| Windows | the compositor — Context only says which context they belong to |
| Settings | the settings window |

A second home for a noun is not a convenience. It is two things to keep in
step, and they will drift: the overview could open a context but not edit,
close or forget one, for as long as both listed them.

### A verb lives on its noun, not on a screen

"Open an application into this context" is a verb on a *context*. It belongs on
a context's row, and nowhere else.

Putting it on an application's row — in the overview, in the sidebar's search
results — is what forced `current_context()` into existence: standing on home
nothing is focused, so "here" had to fall back to the last context visited, and
then the button had to say which one it meant by name because "here" was no
longer true. That is a concept the user has to hold, invented to cover a verb
attached to the wrong noun.

### Surface follows dwell time, not size

| How long you are there | Surface |
| --- | --- |
| Glance and act | the sidebar |
| Go there and work | a workspace of its own |
| Decide and dismiss | a layer-shell overlay |
| Configure and leave | a window |

With one hard constraint on top, from CLAUDE.md: **a layer-shell overlay cannot
hold a popover.** Every `Gtk.DropDown` on one throws the click away, which is
why `widgets.SegmentedChoice` exists. So anything with real controls must not
be an overlay — and settings, which is nothing but controls, is currently the
worst offender.

## The workflows

### Starting a context

**One way: the overview.** Pick an application, get a context named after it,
opened. Everything after that is editing what you already have.

The sidebar's search should not create anything. Typing a name that matches no
context should **carry the text into the overview's application search**, so the
one box in the sidebar always means *find me something* — a context you have, or
an application to make one from. A blank context is the editor's business, and
the editor is reached by editing a context that exists.

### Finding a context you have

**Two, deliberately.** The sidebar's list when it is on screen; the `switch`
overlay when you are working with the keyboard and do not want to leave what you
are in. Same list, two postures. This is the one duplication worth keeping, and
it is a reason to add no third.

### Opening an application into a context

**One place: the context's own row.** From there, the shared catalogue.

This means removing "open here" from the overview's rows and from the sidebar's
application results. It deletes `current_context()`, `LiveState.current_id`, the
came-from fallback and the `into=` naming — all of which exist to paper over the
verb sitting on the wrong noun. The overview always makes a new context: a rule
with no exception to remember.

### Moving a window

**Already right; leave it.** It acts on whatever is focused, so it is a keybind
and a titlebar button, never a panel control — your attention is on the window,
not on the sidebar. `window-left`/`window-right` move within a context,
`move-window` moves across them, `adopt` gives the homeless a home.

### Settings against controls

A **setting** is a decision about the machine that outlives the moment. A
**control** is a decision made in the moment and belongs on screen beside the
thing it changes.

`overview_sort` is the tell in the other direction: which order the catalogue
opens in is a habit, not a policy.

## What this cuts

Twenty-eight settings to about seventeen:

| Cut | Because |
| --- | --- |
| `show_search`, `show_new_context`, `show_saved`, `show_apps`, `show_notes` | Ship one sidebar. The same argument already made for theming: waybar ships one look and a CSS file, and so does Context. |
| `auto_expand_delay_ms`, `collapse_delay_ms`, `poll_seconds` | Pick good numbers and hard-code them. Nobody tunes these twice. |
| `overview_sort` | A habit. Default to Recent, or remember the last used. |
| `scratchpad_global`, `scratchpad_per_context`, `scratchpad_show_both` | Collapse to on and off. There are exactly two scratchpads and no way to make more; which of them is on screen is not four settings' worth of question. |

And **settings becomes a window rather than an overlay** — a task with duration,
made entirely of controls, on a surface where controls do not work.

## The test

Before adding a surface or a setting, one question:

> **Which noun is this a verb on, and where does that noun live?**

Nearly every overlap in this codebase would have been caught by asking it.

## Where this is weakest

Cutting the five `show_*` settings is the change most likely to be regretted.
The declaration this project is actually deployed from turns `show_search` and
`show_apps` off to make the sidebar contexts-only, so the settings are load
bearing today. That is evidence the *default* is wrong rather than that the
settings are right — a sidebar that ships contexts-only would need no such
declaration — but it is the argument to have first, and it is the one place
where this document is asking for something to be taken away that is in use.

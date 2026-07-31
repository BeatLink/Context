# Making an application work with Context

For application developers. Context groups windows into named *contexts* — "work
on the parser", "pay bills" — opens each application at a specific thing, and
arranges the windows. This describes what your application has to do for that to
work properly.

Nothing here is Context-specific in the end. Every requirement is something a
session manager, a workspace tool, or a tiling compositor needs too. Meeting
them makes an application well-behaved for all of them.

The four levels:

| Level                       | What Context can do                                |
| --------------------------- | -------------------------------------------------- |
| **0 — Launchable**   | Open it. Nothing more.                             |
| **1 — Addressable**  | Open it*at* something: a folder, a URL, a file.  |
| **2 — Identifiable** | Know which window belongs to which context.        |
| **3 — Restorable**   | Save what the window became, and rebuild it later. |

Most applications are at level 0 and could reach level 1 with a command-line
flag. Level 2 is where almost everything fails, and it is the level that
matters most.

---

## Level 0 — Launchable

You are here already if you ship a desktop entry. Two things still go wrong.

### 0.1 Exit truthfully

When your application is asked to open and cannot, exit non-zero. When it hands
off to a running instance, exit zero *after* the hand-off has been accepted.

Context distinguishes three outcomes: exited non-zero quickly (a failure,
reported to the user), exited zero quickly (handed off, success), still running
after a grace period (it *is* the application, success). An application that
exits zero having done nothing is indistinguishable from success.

> Measured: a D-Bus-activated terminal, asked to open, raised the window it
> already had and exited zero. The context got no terminal, and the launch
> reported success. Nothing in its desktop entry indicated this.

### 0.2 Do not require a terminal to tell you what went wrong

If startup fails, say so on stderr *and* exit non-zero. Launchers do not read
your log file.

---

## Level 1 — Addressable

**One flag. This is the single highest-value thing most applications could do.**

Accept, as a command-line argument, the thing you should open. Open exactly
that, in one window, and do not also restore an unrelated previous session on
top of it.

| Kind of application | Should accept                                    |
| ------------------- | ------------------------------------------------ |
| Editor / IDE        | a folder, a file, a project or workspace file    |
| Terminal            | a working directory, optionally a command to run |
| Browser             | a list of URLs                                   |
| Document editor     | a file path                                      |
| Chat / mail         | an account, a channel, a mailbox                 |
| Music player        | a playlist or library view                       |

### 1.1 One invocation, one window, all the targets

Given several targets at once, open **one** window containing all of them —
not one window each.

```sh
myapp --new-window a.txt b.txt c.txt      # one window, three tabs
```

> Verified for Firefox: a single invocation with several URLs produces one
> window with one tab each. This is what lets a context say "these three pages
> belong together".

### 1.2 Offer an explicit new-window flag

Provide a flag that forces a new window rather than reusing an existing one, and
make it work even when an instance is already running.

Context exposes a per-application switch for this because the behaviour cannot
be detected, only configured. If your application has no such flag, users have
to turn the switch off and accept that the context cannot own a window.

### 1.3 Do not restore an old session over an explicit target

If you are told to open a specific thing, open that thing. Restoring last
session's windows *as well* means a context that asked for one folder gets four
unrelated ones.

Session restore is correct when launched with no arguments. It is wrong when
launched with them.

### 1.4 Support a separate instance, and say how

Provide a documented way to run genuinely separately from any running instance —
a profile directory, a data directory, an instance name. Context uses this to
give a context its own browser profile.

```sh
firefox  --profile <dir> --new-instance
chromium --user-data-dir=<dir>
```

Make it clear whether the separate instance shares configuration or starts
empty, because that is the trade-off the user is being asked about.

---

## Level 2 — Identifiable

**This is where nearly everything fails, and it is the most valuable level.**

For a context to own a window — adopt it, move it, restore it, or even reliably
count it — the window has to be attributable to the context that caused it.
Wayland provides no general mechanism. `xdg-toplevel-tag-v1` exists for exactly
this and is opt-in per application, which is why this section exists at all.

### 2.1 What does not work, and why you cannot rely on it

**Process IDs.** A launcher that records the process it spawned and matches it
against the window's reported process fails for exactly the applications that
matter.

> Measured on a live session: the spawned Firefox process had exited entirely
> before its window existed, and the window's process was parented to the
> session rather than to the launcher. An Electron editor behaved identically.
> Both re-execute or hand off, so the process started is not the process that
> owns the window.

**`--class` on Wayland.** It is an X11 flag.

> Measured: an Electron application launched with `--class=ctx-test` reported
> its usual class. With `--user-data-dir` forcing a genuinely separate process,
> still its usual class. The flag is accepted and ignored.

### 2.2 What to implement, in order of preference

**Support `xdg-toplevel-tag-v1`.** The correct answer. It exists so a launcher
can hand a window an opaque tag that the compositor reports back. If your
toolkit supports it, expose it; if it does not, ask for it.

**Let the Wayland app-id be set at launch.** The practical answer today. Accept
an environment variable or flag that becomes the `app_id` of the windows that
invocation creates.

> Measured working: `MOZ_APP_REMOTINGNAME=ctx-work firefox …` produces windows
> whose reported class is `ctx-work`. This is the whole mechanism, and it is why
> Firefox is currently the best-integrated application.

If you do this, document two things: that the custom id breaks desktop-entry
association (so launchers can map it back for icons), and whether it forces a
separate instance as a side effect.

**Expose window identity over your own IPC.** If you already have a D-Bus
interface or control socket, publish a stable identifier per window and let it
be set at creation. This is what a browser extension would otherwise have to
provide, and it is better coming from the application.

### 2.3 Report a stable, distinct app-id

If you do nothing else in this section: make sure your Wayland `app_id` matches
your desktop entry's basename, and does not change between runs.

Launchers use it to find your icon, your name, and your entry. An application
reporting `Gtk-Application` or a randomly suffixed id cannot be recognised at
all.

### 2.4 Different windows should be distinguishable

If your application shows several windows, give them distinct, meaningful
titles. A context holding three windows all titled with your application's name
cannot present them to the user, and cannot restore them in the right order.

---

## Level 3 — Restorable

A context is not only opened; it is *reopened*. What the user built up during a
session should come back.

### 3.1 Report what a window currently holds

Expose, over IPC or a query flag, what each window is showing: the folder, the
open files, the URLs, the working directory. This is what lets Context offer
"save what I have open" rather than requiring the user to describe it in advance.

```sh
myapp --list-windows
# id=1  cwd=/home/me/project  files=[src/main.rs, README.md]
```

### 3.2 Accept that state back

Whatever you report, accept as input. The round trip is the point: a context
that can be captured and cannot be restored is half a feature.

### 3.3 Scope session restore to the instance

If a context uses a separate profile or data directory, restore that instance's
session into that instance only. Do not restore a global session into it, and do
not let it write back into the shared one.

### 3.4 Exit cleanly enough to be restarted

When asked to close, save state and exit. A context is closed by asking its
windows to close; an application that ignores that, or that leaves a lock behind
after exiting, breaks reopening.

> Measured: Firefox exits non-zero and silently when its profile is still held
> by a closing instance. A launcher that does not check the status treats the
> failed relaunch as success and the context comes back empty. Release your
> locks before the process exits, not after.

---

## Things that actively break contexts

Independent of level. Each of these has cost real debugging time.

| Behaviour                                             | What it does to a context                         |
| ----------------------------------------------------- | ------------------------------------------------- |
| Exiting zero without doing anything                   | The launch reports success and opens nothing      |
| Never exiting when handing off to a running instance  | The launcher hangs for the application's lifetime |
| Ignoring`--new-window` when already running         | The context cannot own a window                   |
| Restoring a previous session over an explicit target  | The context fills with unrelated windows          |
| A changing or generic`app_id`                       | The application cannot be recognised at all       |
| Holding a profile lock past process exit              | The next open fails, silently                     |
| Refusing to run twice with no way to opt out          | Only one context can ever contain it              |
| Placing your own windows                              | Fights the tiler; the arrangement comes out wrong |
| Opening extra windows unasked (splash, updater, tips) | They land in the context and get arranged         |

---

## A minimal integration checklist

If you want to be well-behaved with as little work as possible:

- [ ] Accept a target as an argument, and open exactly it
- [ ] Provide `--new-window`, and honour it when already running
- [ ] Do not restore a session when given an explicit target
- [ ] Exit non-zero on failure, zero only after a successful hand-off
- [ ] Report a stable `app_id` matching your desktop entry
- [ ] Give windows distinct titles
- [ ] Document how to run a separate instance
- [ ] Release locks before exiting

Those eight get you to level 1 with reliable level-2 identification by class.
Everything beyond is genuinely useful, but that list is the difference between
an application a context can hold and one it cannot.

---

## For compositor authors

Two things would remove most of the difficulty above:

- **Report `xdg-toplevel-tag-v1` tags** in whatever window-query interface you
  expose, so a launcher can attribute a window without guessing.
- **Announce windows as they map**, with identity, class and title, on an event
  stream. Correlating "the next window of this class after I launched it" is the
  only general mechanism available today, and it needs an event to hang off.

Both already exist in Hyprland, which is why it is Context's target.

---

See [README.md](README.md) for what Context does and [ROADMAP.md](ROADMAP.md)
for what is planned.

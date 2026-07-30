# Roadmap

Ordered roughly by dependency. The near-term goal is not more UI — it's making a
context actually *be* something, which means resources and app integration.

## Now

### 1. Resource adapters

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

### 2. Firefox adapter

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

### 3. Window placement

`prepare_launch()` is currently a no-op in both real backends — they rely on
focus-then-launch, which is racy: a slow-mapping app can land on whatever workspace
you switched to since. Fix properly on Hyprland with workspace rules or
workspace-bound `exec` dispatch. Cinnamon largely can't do this, which is fine — it
is a development stand-in, not a target.

Then: layout within a context (the "VS Code docked left, docs right" case), which
needs Hyprland IPC and is a reason to treat Hyprland as the real target.

## Next

### 4. Ephemeral teardown

`ephemeral` is stored and editable but nothing acts on it. Closing an ephemeral
context should remove its workspace, delete its browser profile, and drop it from
the list — carefully, since "throw this away" must never eat real work. Needs a
confirmation path and a definition of what counts as unsaved.

### 5. Session restore

`xdg-session-management-v1` was merged 2026-03-23 after six years, and KWin has a
draft implementation. This is the protocol that finally makes window position and
state restoration possible on Wayland. Once Hyprland supports it, a context can
restore its actual layout rather than re-deriving it from a recipe. Worth tracking;
not actionable yet.

### 6. Login flow

Show the launcher at login: previous contexts, or a new-context page.

One design caution: the current concept assumes every session begins with an
intentional context choice. A lot of real computer use is "unlock and glance at one
thing," and forcing a decision there is exactly the friction that made Activities
feel heavy. Needs a zero-friction default path — resume last context, or a plain
desktop — before this becomes the login entrypoint.

### 7. Hyprland as the primary target

Once resources and placement land, Hyprland becomes the real target and Cinnamon
stays only as a testing fallback. Requires a dev session on a spare VT or VM, since
nesting is impossible (see README).

## Later

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

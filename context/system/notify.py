"""What Context has to say, said through the desktop's notification daemon.

These messages used to be toasts drawn over the launcher's own list. That only
works while the launcher is expanded and on screen, and Context spends most of
its life collapsed to a rail or hidden entirely — so "couldn't open any apps"
was reported to nobody. The desktop already has somewhere for this: swaync,
mako and the rest stack, persist and can be scrolled back through.

A notification with a button needs an action to fire, and `Gio.Notification`
takes an action name rather than a callback. Registering one per message keeps
the callback next to the message it belongs to, which is how the toast worked.
"""

from __future__ import annotations

from gi.repository import Gio, GLib

from context.state import settings
from context.system.logging_setup import get_logger

log = get_logger("notify")


def enabled() -> bool:
    return bool(settings.current().notifications)


def send(
    app,
    key: str,
    title: str,
    body: str = "",
    button: str | None = None,
    on_click=None,
    essential: bool = False,
) -> bool:
    """Show one notification. Returns whether anything was sent.

    `key` identifies the message rather than the occurrence: sending the same
    key again replaces what is on screen, so a burst of launches leaves one
    notification instead of a column of them.

    `essential` bypasses the notifications setting. It is for the messages
    that are the only path to an action — the restart prompt after changing a
    setting that needs one. The setting silences reports; a control is not a
    report.
    """
    if not enabled() and not essential:
        log.debug("notifications are off; not sending %s", key)
        return False
    if app is None:
        return False

    note = Gio.Notification.new(title)
    if body:
        note.set_body(body)
    note.set_priority(Gio.NotificationPriority.LOW)
    if button and on_click is not None:
        name = _register(app, key, on_click)
        if name is not None:
            note.add_button(button, f"app.{name}")

    try:
        app.send_notification(key, note)
    except GLib.Error as exc:  # pragma: no cover - depends on the daemon
        log.warning("could not notify: %s", exc)
        return False
    return True


def _register(app, key: str, on_click) -> str | None:
    """An action for this message's button, replacing any earlier one.

    Named after the message, so the button on a re-sent notification runs the
    callback that came with it rather than the one from last time.
    """
    name = f"notify-{key}".replace(".", "-")
    try:
        if app.lookup_action(name) is not None:
            app.remove_action(name)
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda *_a: on_click())
        app.add_action(action)
    except (AttributeError, TypeError) as exc:  # pragma: no cover - not a GApplication
        log.debug("no action support on %r: %s", app, exc)
        return None
    return name


def withdraw(app, key: str) -> None:
    """Take a notification back down, for one that has been acted on."""
    if app is None:
        return
    try:
        app.withdraw_notification(key)
    except (AttributeError, GLib.Error):  # pragma: no cover
        pass

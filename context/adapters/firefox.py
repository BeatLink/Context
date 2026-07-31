"""Firefox: a profile per context, opened at the context's URLs.

Verified on Firefox 153: one invocation with several URLs opens a single window
with one tab each. There are no window-targeting flags, so a shared profile would
send `--new-tab` to whatever window was last focused — contexts would interleave
tabs and could not reliably reuse their own window. A profile per context gives a
separate instance, and with it tab restore on reopen (Firefox's own session
restore), an isolated cookie jar, and a distinct PID for window matching.

Once a profile exists, later launches pass no URLs so session restore reopens what
was actually left there instead of resetting to the original list.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from ..resources import Resource
from ..store import data_dir
from ..logging_setup import get_logger, traced
from .base import child_env

log = get_logger("adapter.firefox")

APP_IDS = {"firefox.desktop", "firefox-esr.desktop", "org.mozilla.firefox.desktop"}

# How long to wait for a closing instance to release the profile lock, and how
# long to watch a new one before assuming it started successfully.
LOCK_WAIT = 15.0
STARTUP_GRACE = 3.0

# Suppress first-run noise so a new context lands on its URLs, not onboarding.
USER_JS = """\
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.firstRunURL", "");
user_pref("trailhead.firstrun.didSeeAboutWelcome", true);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.sessionstore.resume_from_crash", false);
"""


def profiles_root():
    return data_dir() / "firefox-profiles"


class FirefoxAdapter:
    name = "firefox"

    def handles(self, resource: Resource) -> bool:
        return resource.app_id.strip().casefold() in APP_IDS

    def executable(self) -> str | None:
        return shutil.which("firefox") or shutil.which("firefox-esr")

    def profile_dir(self, resource: Resource, context_id: str):
        if resource.profile:
            return profiles_root() / resource.profile
        return profiles_root() / context_id

    def _is_locked(self, path) -> bool:
        """Whether a live Firefox still holds this profile.

        The lock symlink points at `<ip>:+<pid>`; a stale one from a crash names
        a pid that is gone, and Firefox recovers from those by itself.
        """
        lock = path / "lock"
        try:
            target = os.readlink(lock)
        except OSError:
            return False
        _, _, pid = target.rpartition("+")
        if not pid.isdigit():
            return False
        try:
            os.kill(int(pid), 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def _await_unlocked(self, path) -> None:
        """Give a closing instance time to release the profile."""
        deadline = time.monotonic() + LOCK_WAIT
        while self._is_locked(path) and time.monotonic() < deadline:
            time.sleep(0.25)

    def _prepare_profile(self, path) -> bool:
        """Create the profile if absent. Returns True if it is new."""
        if path.exists():
            return False
        path.mkdir(parents=True, exist_ok=True)
        (path / "user.js").write_text(USER_JS)
        return True

    def _launch_in_main_profile(self, binary: str, resource: Resource) -> None:
        """Open the context's URLs in the user's existing Firefox.

        The main profile is almost always already running and a profile can only
        be held by one process, so a new instance is impossible. Instead each URL
        is handed to the running Firefox, which opens it in a new window — the
        first URL creates the window and the rest become tabs beside it.
        """
        urls = resource.urls or ["about:blank"]
        first, rest = urls[0], urls[1:]

        opened = subprocess.run(
            [binary, "--new-window", first],
            capture_output=True,
            text=True,
            env=child_env(),
        )
        if opened.returncode != 0:
            raise LookupError(
                f"firefox exited with status {opened.returncode} opening {first}"
            )
        for url in rest:
            subprocess.run(
                [binary, "--new-tab", url],
                capture_output=True,
                text=True,
                env=child_env(),
            )

    @traced(log)
    def launch(self, resource: Resource, context_id: str) -> None:
        binary = self.executable()
        if binary is None:
            raise LookupError("firefox is not installed")

        if resource.uses_main_profile:
            self._launch_in_main_profile(binary, resource)
            return

        path = self.profile_dir(resource, context_id)
        is_new = self._prepare_profile(path)

        command = [binary, "--profile", str(path), "--new-instance"]
        if is_new:
            # Only seed URLs on first run; afterwards session restore wins.
            command.extend(resource.urls)
        elif not resource.urls:
            command.append("about:blank")

        self._await_unlocked(path)

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=child_env(),
            )
        except OSError as exc:
            raise LookupError(f"could not start firefox: {exc}") from exc

        # Firefox exits immediately, silently, and non-zero when the profile is
        # still held by a previous instance. Without this check a failed relaunch
        # looks like a success and the context comes back empty.
        try:
            code = process.wait(timeout=STARTUP_GRACE)
        except subprocess.TimeoutExpired:
            return  # Still running, which is what success looks like.
        if code != 0:
            hint = (
                "its profile may still be in use"
                if code == 1
                else f"it exited abnormally (signal {-code})"
                if code < 0
                else "it crashed on startup"
            )
            raise LookupError(f"firefox exited with status {code}; {hint}")

    def describe(self, resource: Resource) -> str:
        if not resource.urls:
            summary = "no URLs yet"
        elif len(resource.urls) == 1:
            summary = _pretty(resource.urls[0])
        else:
            summary = f"{_pretty(resource.urls[0])} +{len(resource.urls) - 1} more"
        if resource.uses_main_profile:
            summary += " · main profile"
        return summary

    @traced(log)
    def teardown(self, resource: Resource, context_id: str) -> None:
        if resource.uses_main_profile:
            # Never touch the user's own profile.
            return
        path = self.profile_dir(resource, context_id)
        root = profiles_root()
        # Refuse to remove anything outside the profiles root.
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def _pretty(url: str) -> str:
    trimmed = url.split("://", 1)[-1]
    return trimmed[:-1] if trimmed.endswith("/") else trimmed

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

import shutil
import subprocess

from ..resources import Resource
from ..store import data_dir

APP_IDS = {"firefox.desktop", "firefox-esr.desktop", "org.mozilla.firefox.desktop"}

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

    def _prepare_profile(self, path) -> bool:
        """Create the profile if absent. Returns True if it is new."""
        if path.exists():
            return False
        path.mkdir(parents=True, exist_ok=True)
        (path / "user.js").write_text(USER_JS)
        return True

    def launch(self, resource: Resource, context_id: str) -> None:
        binary = self.executable()
        if binary is None:
            raise LookupError("firefox is not installed")

        path = self.profile_dir(resource, context_id)
        is_new = self._prepare_profile(path)

        command = [binary, "--profile", str(path), "--new-instance"]
        if is_new:
            # Only seed URLs on first run; afterwards session restore wins.
            command.extend(resource.urls)
        elif not resource.urls:
            command.append("about:blank")

        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise LookupError(f"could not start firefox: {exc}") from exc

    def describe(self, resource: Resource) -> str:
        if not resource.urls:
            return "no URLs yet"
        if len(resource.urls) == 1:
            return _pretty(resource.urls[0])
        return f"{_pretty(resource.urls[0])} +{len(resource.urls) - 1} more"

    def teardown(self, resource: Resource, context_id: str) -> None:
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

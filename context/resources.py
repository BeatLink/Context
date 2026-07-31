"""What a context opens.

A resource is one application plus whatever it should be pointed at. The bare
`app_id` case (no options) is equivalent to the old flat app list, which is how
pre-resource context files keep working.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


PROFILE_DEDICATED = "dedicated"
PROFILE_MAIN = "main"
PROFILE_MODES = (PROFILE_DEDICATED, PROFILE_MAIN)


@dataclass
class Resource:
    app_id: str
    urls: list[str] = field(default_factory=list)
    path: str | None = None
    profile: str | None = None
    profile_mode: str = PROFILE_DEDICATED
    command: str | None = None
    # Compatibility switches. Apps differ in how they behave when already
    # running, and the right answer is per-app rather than something Context can
    # infer, so each is exposed rather than guessed.
    force_new_window: bool = True
    single_instance: bool = False

    @classmethod
    def from_dict(cls, raw: dict) -> "Resource":
        known = set(cls.__dataclass_fields__)
        data = {k: v for k, v in raw.items() if k in known}
        data.setdefault("app_id", "")
        if not isinstance(data.get("urls"), list):
            data["urls"] = []
        data["urls"] = [str(u) for u in data["urls"] if str(u).strip()]
        if data.get("profile_mode") not in PROFILE_MODES:
            data["profile_mode"] = PROFILE_DEDICATED
        for flag in ("force_new_window", "single_instance"):
            if flag in data:
                data[flag] = bool(data[flag])
        return cls(**data)

    @property
    def opens_its_own_window(self) -> bool:
        """Whether launching should insist on a new window.

        Off for apps that only ever run one instance, where asking for a new
        window either fails or is ignored, and Context should adopt the window
        that already exists instead.
        """
        return self.force_new_window and not self.single_instance

    @property
    def uses_main_profile(self) -> bool:
        return self.profile_mode == PROFILE_MAIN

    # Booleans are written even when False: dropping falsy values would lose a
    # deliberately disabled switch, which would then come back on next load.
    ALWAYS_WRITE = ("force_new_window", "single_instance")

    def to_dict(self) -> dict:
        return {
            k: v
            for k, v in asdict(self).items()
            if k in self.ALWAYS_WRITE or v not in (None, [], "")
        }

    @property
    def is_configured(self) -> bool:
        return bool(self.urls or self.path)


def parse_resources(raw: object) -> list[Resource]:
    """Read a stored resource list, tolerating the legacy list-of-app-ids form."""
    if not isinstance(raw, list):
        return []
    resources: list[Resource] = []
    for entry in raw:
        if isinstance(entry, str):
            if entry.strip():
                resources.append(Resource(app_id=entry))
        elif isinstance(entry, dict):
            resource = Resource.from_dict(entry)
            if resource.app_id:
                resources.append(resource)
    return resources


def split_urls(text: str) -> list[str]:
    """Parse the URL entry field: one per line, or comma separated."""
    parts: list[str] = []
    for line in text.replace(",", "\n").splitlines():
        candidate = line.strip()
        if candidate:
            parts.append(normalize_url(candidate))
    return parts


def normalize_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        return url
    if "://" in url or url.startswith(("about:", "mailto:", "file:")):
        return url
    return f"https://{url}"

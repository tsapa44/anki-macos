"""Configuration for the Anki Daily Blocker.

The config file is root-owned in a real install so a non-root user cannot quietly
shrink the Blocklist or the quota while the Block is in force. Paths can be
overridden (config file, hosts file, state file) so the whole thing runs against a
sandbox during development and tests.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

DEFAULT_CONFIG_PATH = os.environ.get(
    "ANKIBLOCK_CONFIG", "/usr/local/etc/ankiblock/config.json"
)

# The user's curated time-sinks.
DEFAULT_BLOCKLIST = [
    "meduza.io",
    "cybersport.ru",
    "zerkalo.io",
    "instagram.com",
    "x.com",
    "twitch.tv",
    "web.telegram.org",
    "telegram.org",
    "steampowered.com",
    "linkedin.com",
    "youtube.com",
    "tiktok.com",
    "facebook.com",
]


@dataclass
class Config:
    # Detection
    anki_connect_url: str = "http://127.0.0.1:8765"
    daily_quota: int = 20  # Reviews (revlog answer events), see CONTEXT.md
    day_cutoff_hour: int = 4  # matches Anki's default day rollover

    # Enforcement
    blocklist: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCKLIST))
    hosts_path: str = "/etc/hosts"
    flush_dns: bool = True

    # Behaviour
    poll_interval_seconds: int = 30
    emergency_delay_seconds: int = 900  # 15 min friction on the Emergency unlock

    # State
    state_path: str = "/usr/local/var/ankiblock/state.json"

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        path = path or DEFAULT_CONFIG_PATH
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            # Surface typos rather than silently ignoring a misspelled key.
            raise ValueError(f"Unknown config keys in {path}: {sorted(unknown)}")
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: str | None = None) -> None:
        path = path or DEFAULT_CONFIG_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)
            f.write("\n")
